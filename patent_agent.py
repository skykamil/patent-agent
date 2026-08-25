import os
import sys
import uuid
import json
import requests
import xml.etree.ElementTree as ET
from openai import OpenAI
from dotenv import load_dotenv
from datetime import datetime, timedelta
from logs_db import init_db, log_tool_call, update_final_response
from openai.types.responses.function_tool_param import FunctionToolParam

load_dotenv()

ns = {"ex": "http://www.epo.org/exchange", "ops": "http://ops.epo.org"}

epo_token = None
epo_token_expiry = None

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

tools: list[FunctionToolParam] = [
    {
        "type": "function",
        "name": "search_patent",
        "description": "Searches for patents in the EPO database by title, applicant name, publication number, application number, or publication date. Use when the user wants to find or look up a patent.",
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
            },
            "required": ["ti", "pa", "pn", "ap", "pd_from", "pd_to"],
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
        "description": "Calculates the expiration date of a patent based on its filing date. Use when the user wants to know when a patent will expire.",
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

def get_title(patent):
    title = None
    ref = patent.find(f'.//ex:bibliographic-data', ns)
    titles = ref.findall('ex:invention-title', ns)
    for case in titles:
        if case.get("lang") == "en":
            title = case.text
    return title

def get_applicants(patent):
    applicant_elements = patent.findall(f'.//ex:applicant', ns)
    applicants = []
    for applicant in applicant_elements:
        if applicant.get("data-format") == "epodoc":
            name_elem = applicant.find('.//ex:name', ns)
            applicants.append(name_elem.text)
    return applicants

def search_patent(ti=None, pa=None, pn=None, ap=None, pd_from=None, pd_to=None):
    token = get_epo_access_token()
    headers = {"Authorization": f"Bearer {token}"}
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
    root = ET.fromstring(r.text)
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
    return publications

def get_patent_details(pn):
    details = {}
    token = get_epo_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"https://ops.epo.org/rest-services/published-data/publication/epodoc/{pn}/biblio", headers=headers)
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


def run_agent(input_list, run_id):
    logged_ids = []
    user_input = input_list[-1]["content"]
    actual_calls = []
    response = client.responses.create(
            model="gpt-5.6-luna",
            tools=tools,
            input=input_list,
    )
    print(response.output)
    input_list += response.output
    i=0
    while any(item.type == "function_call" for item in response.output) and i < 3:
        for item in response.output:
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
                            except Exception as e:
                                patent_records = {"error": f"Could not complete {item.name}: {str(e)}"}
                                status = "error"
                                error_message = str(e)
                            else:
                                status = "success"
                                error_message = None
                            actual_calls.append({"name": item.name, "args": args})
                            function_call_output = {
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
        input_list += response.output
        i += 1
    final_response = response.output_text
    print(final_response)
    for log_id in logged_ids:
        update_final_response(log_id, final_response)
    if not actual_calls:    
        log_tool_call(run_id, user_input, tool_name=None, arguments=None, tool_output=None, status="no_tool_call", error_message=None, final_response=final_response)
    return actual_calls

def run_repl():
    run_id = str(uuid.uuid4())
    input_list:list = []
    while True:
        user_input = input("N - New chat\nE - Exit\nHow can I help you?\n\n").lower().strip()
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
            run_agent(input_list, run_id)

def run_eval():
    eval_set = [
        {
            "input": "Search for a patent by applicant name: Siemens",
            "expected_calls": [
                {"name": "search_patent", "args": {"pa": "Siemens"}}
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
            ]
        },
        {
            "input": "When will the patent EP1000000 expire?",
            "expected_calls": [
                {"name": "get_patent_details", "args": {"pn": "EP1000000"}},
                {"name": "expiration_date", "args": {"filing_date": "19991108"}}
            ]
        },
        {
            "input": "What is 2 + 2?",
            "expected_calls": []
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
    for n, case in enumerate(eval_set, start=1):
        run_id = str(uuid.uuid4())
        print(f"-------{n}------")
        input_list = [{"role": "user", "content": case["input"]}]
        agent = run_agent(input_list, run_id)
        print(case["input"], "→", agent)
        if len(agent) == len(case["expected_calls"]) and all(expected_call["name"] == actual_call["name"] and expected_call["args"].items() <= actual_call["args"].items()
                for actual_call, expected_call in zip(agent, case["expected_calls"])):
                    count += 1

    print(f"{count}/{len(eval_set)}")

def main():

    init_db()

    if "--eval" in sys.argv:
        run_eval()
    else:
        run_repl()

if __name__ == "__main__":
    main()