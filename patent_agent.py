import os
import sys
import uuid
import json
import requests
import xml.etree.ElementTree as ET
from typing import cast
from openai import OpenAI
from dotenv import load_dotenv
from datetime import datetime, timedelta
from logs_db import init_db, log_tool_call, update_final_response
from openai.types.responses.function_tool_param import FunctionToolParam
from openai.types.responses.response_input_param import ResponseInputParam, ResponseInputItemParam, FunctionCallOutput

load_dotenv()

ns = {"ex": "http://www.epo.org/exchange", "ops": "http://ops.epo.org"}

epo_token = None
epo_token_expiry = None

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

tools: list[FunctionToolParam] = [
    {
        "type": "function",
        "name": "search_patent",
        "description": "Searches for patents in the EPO database by title, applicant name, publication number, application number, publication date, and result page. Use when the user wants to find or look up patents. Each page contains up to 25 results. When presenting search results, list EVERY publication number returned in 'results' for the current page; do not sample, summarize, or omit any returned result. Clearly state the total number of matching records, the current page and total pages, the number of results shown, and the number of available pages. If 'truncated' is true, explain that OPS limits retrieval to the first 2,000 records, so only the first 80 pages of 25 results are accessible even when more matches exist.",
        "parameters": {
            "type": "object",
            "properties": {
                "ti": {
                    "type": ["string", "null"],
                    "description": "the publication title in English"
                },
                "pa": {
                    "type": ["string", "null"],
                    "description": "an applicant name"
                },
                "pn": {
                    "type": ["string", "null"],
                    "description": "the publication number in any format"
                },
                "ap": {
                    "type": ["string", "null"],
                    "description": "the application number in any format"
                },
                "pd_from": {
                    "type": ["string", "null"],
                    "description": "Start of the publication date range, in YYYYMMDD format. If the user wants publications after a certain date with no end date, provide only this. Do not guess or fill in pd_to yourself — the system handles the missing bound automatically."
                },
                "pd_to": {
                    "type": ["string", "null"],
                    "description": "End of the publication date range, in YYYYMMDD format. If the user wants publications before a certain date with no start date, provide only this. For an exact single date, set both pd_from and pd_to to the same value. Do not guess or fill in pd_from yourself — the system handles the missing bound automatically."
                },
                "page": {
                    "type": ["integer", "null"],
                    "description": "Results page to retrieve, starting from 1. Each page contains up to 25 results. Null defaults to the first page."
                },
            },
            "required": ["ti", "pa", "pn", "ap", "pd_from", "pd_to", "page"],
            "additionalProperties": False
        },
        "strict": True
    },
    {
        "type": "function",
        "name": "get_patent_details",
        "description": "Retrieves detailed information about a specific patent using its publication number. Use ONLY when the user wants to get more information about a specific patent and give its number. Do not call this automatically after search_patent to enrich search results, unless the user explicitly asks for details of a specific result.",
        "parameters": {
            "type": "object",
            "properties": {
                "pn": {
                    "type": "string",
                    "description": "the publication number in any format"
                }
            },
            "required": ["pn"],
            "additionalProperties": False
        },
        "strict": True
    },
    {
        "type": "function",
        "name": "expiration_date",
        "description": "Calculates a simplified 20-year patent term date from the filing date. This is NOT a legal determination of the patent's actual expiration or current legal status and does not account for extensions, adjustments, lapse, revocation, or other legal events. Use when the user asks when a patent may expire. In the final response, clearly state that the returned date is a simplified filing-date-plus-20-years calculation and not a verified legal expiration date.",
        "parameters": {
            "type": "object",
            "properties": {
                "filing_date": {
                    "type": "string",
                    "description": "the filing date in YYYYMMDD format"
                }
            },
            "required": ["filing_date"],
            "additionalProperties": False
        },
        "strict": True
    },
]

def get_epo_access_token():
    global epo_token, epo_token_expiry
    consumer_key = os.getenv("EPO_CONSUMER_KEY")
    secret_consumer = os.getenv("EPO_CONSUMER_SECRET")
    assert consumer_key is not None, "Missing EPO_CONSUMER_KEY in .env"
    assert secret_consumer is not None, "Missing EPO_CONSUMER_SECRET in .env"
    if epo_token is not None and epo_token_expiry is not None and datetime.now() < epo_token_expiry:
        return epo_token
    else:
        r = requests.post('https://ops.epo.org/3.2/auth/accesstoken', auth=(consumer_key, secret_consumer), data={'grant_type': 'client_credentials'})
        r.raise_for_status()
        data = r.json()
        epo_token = data["access_token"]
        now = datetime.now()
        epo_token_expiry = now + timedelta(seconds=int(data["expires_in"])-30)
        return epo_token

def get_epodoc_value(patent, container, child_tag):
    ref = patent.find(f'.//ex:{container}', ns)
    doc_ids = ref.findall('ex:document-id', ns)
    for doc_id in doc_ids:
        if doc_id.get("document-id-type") == "epodoc":
            number = doc_id.find(f'ex:{child_tag}', ns)
            return number.text

def get_filtered_values(patent, element_tag, attribute_name, attribute_value, child_tag):
    matching_elements = patent.findall(f'.//ex:{element_tag}', ns)
    values = []
    for element in matching_elements:
        if element.get(attribute_name) == attribute_value:
            element_name = element.find(f'.//ex:{child_tag}', ns)
            values.append(element_name.text)
    return values


def get_title(patent):
    title = None
    ref = patent.find(f'.//ex:bibliographic-data', ns)
    titles = ref.findall('ex:invention-title', ns)
    for case in titles:
        if case.get("lang") == "en":
            title = case.text
    return title

def get_applicants(patent):
    return get_filtered_values(patent, 'applicant', 'data-format', 'epodoc', 'name')

def search_patent(ti=None, pa=None, pn=None, ap=None, pd_from=None, pd_to=None, page=None):
    if page is None:
        page = 1
    if page < 1 or page > 80:
        raise ValueError("Page must be between 1 and 80")
    range_start = (page - 1) * 25 + 1
    range_stop = range_start + 24
    token = get_epo_access_token()
    headers = {"Authorization": f"Bearer {token}", "X-OPS-Range": f"{range_start}-{range_stop}"}
    query = []
    pd = ""
    if (pd_from is not None and pd_to is not None):
        pd = f'{pd_from} {pd_to}'
    elif pd_from is not None and pd_to is None:
        pd = f'{pd_from} {datetime.now().strftime("%Y%m%d")}'
    elif pd_to is not None and pd_from is None:
        pd = f'19000101 {pd_to}'
    else:
        pd = None
    params = {"ti": ti, "pa": pa, "pn": pn, "ap": ap, "pd": pd}
    for name, value in params.items():
        if value is not None:
            query.append(f'{name}="{value}"')
    query_string = " and ".join(query)
    r = requests.get("https://ops.epo.org/rest-services/published-data/search", headers=headers, params={"q": query_string})
    r.raise_for_status()
    root = ET.fromstring(r.text)
    search_info = root.find('.//ops:biblio-search', ns)
    assert search_info is not None, "Missing biblio-search"
    result_count = search_info.get("total-result-count")
    assert result_count is not None, "Missing total-result-count"
    result_count = int(result_count)
    total_pages = (result_count + 24) // 25
    available_pages = min(total_pages, 80)
    truncated = result_count > 2000
    results = root.findall('.//ops:publication-reference', ns)
    publications = []
    for result in results:
        country = result.find('.//ex:country', ns)
        number = result.find('.//ex:doc-number', ns)
        kind = result.find('.//ex:kind', ns)
        assert country is not None, "Missing country"
        assert number is not None, "Missing number"
        assert kind is not None, "Missing kind"
        assert country.text is not None, "Missing country"
        assert number.text is not None, "Missing number"
        assert kind.text is not None, "Missing kind"
        publications.append(country.text + number.text + kind.text)
    return {
        "results": publications,
        "total_results": result_count,
        "page": page,
        "total_pages": total_pages,
        "available_pages": available_pages,
        "truncated": truncated        
    }

def get_patent_details(pn):
    details = {}
    token = get_epo_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"https://ops.epo.org/rest-services/published-data/publication/epodoc/{pn}/biblio", headers=headers)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    documents = root.findall('.//ex:exchange-document', ns)
    patent = None
    for doc in documents:
        if doc.get("kind") == "B1":
            patent = doc
    if patent is None:
        for doc in documents:
            if doc.get("kind") == "A1":
                patent = doc
    details["publication_number"] = get_epodoc_value(patent, "publication-reference", "doc-number")
    details["filing_date"] = get_epodoc_value(patent, "application-reference", "date")
    details["title"] = get_title(patent)
    details["applicants"] = get_applicants(patent)
    return details

def expiration_date(filing_date):
      parsed = datetime.strptime(filing_date, "%Y%m%d")
      expiration = parsed.replace(year=parsed.year + 20)
      return expiration.strftime("%Y%m%d")


def run_agent(input_list: ResponseInputParam, run_id: str, user_input: str):
    logged_ids = []
    actual_calls = []
    tool_outputs = []
    response = client.responses.create(
            model="gpt-5.6-luna",
            tools=tools,
            input=input_list,
    )
    print(response.output)
    for item in response.output:
        input_list.append(cast(ResponseInputItemParam, item))
    i=0
    while any(item.type == "function_call" for item in response.output) and i < 3:
        for item in response.output:
                item.model_dump()
                if item.type == "function_call":
                        if item.name in ["search_patent", "get_patent_details", "expiration_date"]:
                            args = json.loads(item.arguments)
                            try:
                                if item.name == "search_patent":
                                    patent_records = search_patent(**args)
                                elif item.name == "get_patent_details":
                                    patent_records = get_patent_details(**args)
                                else:
                                    patent_records = expiration_date(**args)
                            except requests.exceptions.RequestException as e:
                                patent_records = {"error": f"Network error in {item.name}: {str(e)}"}
                                status = "network_error"
                                error_message = str(e)
                            except ET.ParseError as e:
                                patent_records = {"error": f"Could not parse response for {item.name}: {str(e)}"}
                                status = "parse_error"
                                error_message = str(e)
                            except Exception as e:
                                patent_records = {"error": f"Could not complete {item.name}: {str(e)}"}
                                status = "error"
                                error_message = str(e)
                            else:
                                status = "success"
                                error_message = None
                            actual_calls.append({"name": item.name, "args": args})
                            tool_outputs.append({"name": item.name, "output": patent_records})
                            function_call_output: FunctionCallOutput = {
                                "type": "function_call_output",
                                "call_id": item.call_id,
                                "output": json.dumps(patent_records)
                            }
                            input_list.append(function_call_output)
                            log_id = log_tool_call(run_id, user_input, item.name, json.dumps(args), json.dumps(patent_records), status, error_message, final_response=None)
                            logged_ids.append(log_id)
        response = client.responses.create(
                model="gpt-5.6-luna",
                tools=tools,
                input=input_list,
        )
        for item in response.output:
            input_list.append(cast(ResponseInputItemParam, item))
        i += 1
    final_response = response.output_text
    print(final_response)
    for log_id in logged_ids:
        update_final_response(log_id, final_response)
    if not actual_calls:    
        log_tool_call(run_id, user_input, tool_name=None, arguments=None, tool_output=None, status="no_tool_call", error_message=None, final_response=final_response)
    return actual_calls, tool_outputs, final_response

def run_repl():
    run_id = str(uuid.uuid4())
    input_list: ResponseInputParam = []
    while True:
        user_input = input("\nN - New chat\nE - Exit\nHow can I help you?\n\n").lower().strip()
        if user_input == "n":
            input_list = []
            run_id = str(uuid.uuid4())
            continue
        elif user_input == "e":
            break
        else:
            input_list.append({
                    "role": "user",
                    "content": user_input
                    })
            run_agent(input_list, run_id, user_input)

def run_eval():
    eval_set = [
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
            "input": "What is 2 + 2?",
            "expected_calls": [],
            "expected_response_contains": ["4"]
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

    count = 0
    response_count = 0
    response_cases = 0
    for n, case in enumerate(eval_set, start=1):
        run_id = str(uuid.uuid4())
        print(f"-------{n}------")
        user_input = case["input"]
        assert isinstance(user_input, str)
        input_list: ResponseInputParam = [{"role": "user", "content": user_input}]
        actual_calls, tool_outputs, final_response = run_agent(input_list, run_id, user_input)
        expected_response = case.get("expected_response_contains", [])
        expected_response_any = case.get("expected_response_any", [])
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
        if expected_response or has_search_output:
            response_cases += 1
            if response_pass:
                response_count += 1
        print(case["input"], "→", actual_calls)
        if len(actual_calls) == len(case["expected_calls"]) and all(expected_call["name"] == actual_call["name"] and expected_call["args"].items() <= actual_call["args"].items()
            for actual_call, expected_call in zip(actual_calls, case["expected_calls"])):
                count += 1

    print(f"Tool-call eval: {count}/{len(eval_set)}")
    print(f"Final-response eval: {response_count}/{response_cases}")

def main():

    init_db()

    if "--eval" in sys.argv:
        run_eval()
    else:
        run_repl()

if __name__ == "__main__":
    main()