import uuid
from openai.types.responses.response_input_param import ResponseInputParam

EVAL_SET = [
    {
        "input": "Search for a patent by applicant name: Siemens",
        "expected_calls": [
            {"name": "search_patent", "args": {"pa": "Siemens"}}
        ]
    },
    {
        "input": "Search for Siemens patents, page 2",
        "expected_calls": [
            {"name": "search_patent", "args": {"pa": "Siemens", "page": 2}}
        ]
    },
    {
        "input": "Find patents with the title: wireless charging",
        "expected_calls": [
            {"name": "search_patent", "args": {"ti": "wireless charging"}}
        ]
    },
    {
        "input": "Search for publication number EP1000000",
        "expected_calls": [
            {"name": "search_patent", "args": {"pn": "EP1000000"}}
        ],
        "expected_response_contains": [
            "EP1000000A1"
        ]
    },
    {
        "input": "Look up application number EP19990203729",
        "expected_calls": [
            {"name": "search_patent", "args": {"ap": "EP19990203729"}}
        ]
    },
    {
        "input": "Get details for publication number: EP1000000",
        "expected_calls": [
            {"name": "get_patent_details", "args": {"pn": "EP1000000"}}
        ],
        "expected_response_contains": [
            "EP1000000",
            "Apparatus for manufacturing green bricks",
            "1999",
            "Boer Beheer Nijmegen",
            "Beheermij De Boer Nijmegen"
        ]
    },
    {
        "input": "When will the patent EP1000000 expire?",
        "expected_calls": [
            {"name": "get_patent_details", "args": {"pn": "EP1000000"}},
            {"name": "expiration_date", "args": {"filing_date": "19991108"}}
        ],
        "expected_response_contains": [
            "2019",
            "filing date"
        ],
        "expected_response_any": [
            "simplified",
            "not a verified legal expiration",
            "not a legal determination"
        ]
    },
    {
        "input": "Design an API developer portal architecture",
        "expected_calls": [],
        "expected_response_contains": ["patent"]
    },
    {
        "input": "Find patents with the title wireless charging published from January 1, 2024",
        "expected_calls": [
            {"name": "search_patent", "args": {"ti": "wireless charging", "pd_from": "20240101"}}
        ]
    },
    {
        "input": "Find patents with the title wireless charging published up to January 1, 2020",
        "expected_calls": [
            {"name": "search_patent", "args": {"ti": "wireless charging", "pd_to": "20200101"}}
        ]
    },
    {
        "input": "Find patents with the title wireless charging published between January 1, 2020 and June 1, 2020",
        "expected_calls": [
            {"name": "search_patent", "args": {"ti": "wireless charging", "pd_from": "20200101", "pd_to": "20200601"}}
        ]
    },
]

def run_eval(agent_runner):
    count = 0
    response_count = 0
    response_cases = 0
    for n, case in enumerate(EVAL_SET, start=1):
        run_id = str(uuid.uuid4())
        print(f"-------{n}------")
        user_input = case["input"]
        assert isinstance(user_input, str)
        input_list: ResponseInputParam = [{"role": "user", "content": user_input}]
        actual_calls, tool_outputs, final_response = agent_runner(input_list, run_id, user_input)
        expected_response = case.get("expected_response_contains", [])
        expected_response_any = case.get("expected_response_any", [])
        expects_search = False
        for call in case["expected_calls"]:
            if call["name"] == "search_patent":
                expects_search = True
        response_pass = all(expected.lower() in final_response.lower() for expected in expected_response)
        if expected_response_any:
            response_pass = response_pass and any(expected.lower() in final_response.lower() for expected in expected_response_any)
        has_search_output = False
        for tool_output in tool_outputs:
            if tool_output["name"] == "search_patent":
                has_search_output = True
                output = tool_output["output"]
                if isinstance(output, dict) and "results" in output:
                    response_pass = response_pass and all(publication.lower() in final_response.lower() for publication in output["results"])
                    normalized_response = final_response.lower().replace(",", "").replace("*", "").replace(":", "")
                    page_info = f"page {output['page']} of {output['total_pages']}"
                    response_pass = (response_pass and page_info in normalized_response and str(output["total_results"]) in normalized_response and str(output["available_pages"]) in normalized_response)
                    if output["truncated"]:
                        response_pass = response_pass and "2000" in normalized_response and ("limit" in normalized_response or "truncat" in normalized_response)
                else:
                    response_pass = False
        if expects_search and not has_search_output:
            response_pass = False
        if expected_response or expected_response_any or expects_search:
            response_cases += 1
            if response_pass:
                response_count += 1
            else:
                print(n, final_response, expected_response, expected_response_any)
        print(case["input"], "→", actual_calls)
        if len(actual_calls) == len(case["expected_calls"]) and all(expected_call["name"] == actual_call["name"] and expected_call["args"].items() <= actual_call["args"].items()
            for actual_call, expected_call in zip(actual_calls, case["expected_calls"])):
                count += 1

    print(f"Tool-call eval: {count}/{len(EVAL_SET)}")
    print(f"Final-response eval: {response_count}/{response_cases}")
