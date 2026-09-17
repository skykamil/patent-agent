import os
from openai import OpenAI
import json
import requests
import xml.etree.ElementTree as ET
from dotenv import load_dotenv
from typing import cast
from openai.types.responses.response_input_param import (ResponseInputParam, ResponseInputItemParam, FunctionCallOutput)
from logs_db import log_tool_call, update_final_response
from errors import (EPOServiceError, EPOTimeoutError, EPOConnectionError, EPOUpstreamError, EPORateLimitError, AgentInternalError, AgentRuntimeLimitError)
from tools import tools, expiration_date
from epo_client import (current_run_id, current_tool_name, current_tool_call_id, search_patent, get_patent_details)

OPENAI_TIMEOUT = 60.0
MAX_AGENT_ITERATIONS = 3
MAX_TOOL_CALLS = 30

load_dotenv()

_client: OpenAI | None = None

def get_openai_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=OPENAI_TIMEOUT, max_retries=2)
    return _client

def run_agent(input_list: ResponseInputParam, run_id: str, user_input: str, token_usage: dict[str, int] | None = None):
    client = get_openai_client()
    if token_usage is None:
        token_usage = {
            "input": 0,
            "output": 0,
        }
    logged_ids = []
    actual_calls = []
    tool_outputs = []
    tool_call_count = 0
    response = client.responses.create(
            model="gpt-5.6-luna",
            tools=tools,
            input=input_list,
    )
    usage = response.usage
    if usage is not None:
        token_usage["input"] += usage.input_tokens
        token_usage["output"] += usage.output_tokens
    for item in response.output:
        input_list.append(cast(ResponseInputItemParam, item))
    i = 0
    while any(item.type == "function_call" for item in response.output) and i < MAX_AGENT_ITERATIONS:
        for item in response.output:
                if item.type == "function_call":
                        if item.name in ["search_patent", "get_patent_details", "expiration_date"]:
                            tool_call_count += 1
                            if tool_call_count > MAX_TOOL_CALLS:
                                raise AgentRuntimeLimitError("Agent reached maximum tool call limit")
                            args = json.loads(item.arguments)
                            run_id_token = current_run_id.set(run_id)
                            tool_name_token = current_tool_name.set(item.name)
                            tool_call_id_token = current_tool_call_id.set(item.call_id)
                            try:
                                if item.name == "search_patent":
                                    patent_records = search_patent(**args)
                                elif item.name == "get_patent_details":
                                    patent_records = get_patent_details(**args)
                                else:
                                    patent_records = expiration_date(**args)
                            except requests.exceptions.Timeout as e:
                                log_tool_call(
                                    run_id=run_id,
                                    user_input=user_input,
                                    tool_name=item.name,
                                    arguments=json.dumps(args),
                                    tool_output=None,
                                    status="network_error",
                                    error_message=str(e),
                                    final_response=None,
                                )
                                raise EPOTimeoutError("EPO request timed out") from e
                            except requests.exceptions.ConnectionError as e:
                                log_tool_call(
                                    run_id=run_id,
                                    user_input=user_input,
                                    tool_name=item.name,
                                    arguments=json.dumps(args),
                                    tool_output=None,
                                    status="network_error",
                                    error_message=str(e),
                                    final_response=None,
                                )
                                raise EPOConnectionError("EPO connection error") from e
                            except requests.exceptions.HTTPError as e:
                                status_code = e.response.status_code
                                if status_code in [401, 403]:
                                    raise EPOUpstreamError("EPO authentication failed") from e
                                if status_code == 429:
                                    log_tool_call(
                                        run_id=run_id,
                                        user_input=user_input,
                                        tool_name=item.name,
                                        arguments=json.dumps(args),
                                        tool_output=None,
                                        status="network_error",
                                        error_message=str(e),
                                        final_response=None,
                                    )
                                    raise EPORateLimitError("EPO rate limit exceeded") from e
                                elif 500 <= status_code < 600:
                                    log_tool_call(
                                        run_id=run_id,
                                        user_input=user_input,
                                        tool_name=item.name,
                                        arguments=json.dumps(args),
                                        tool_output=None,
                                        status="network_error",
                                        error_message=str(e),
                                        final_response=None,
                                    )
                                    raise EPOUpstreamError(f"EPO returned HTTP {status_code}") from e
                                else:
                                    patent_records = {"error": f"EPO returned HTTP {status_code}: {str(e)}"}
                                    status = "network_error"
                                    error_message = str(e)
                            except ET.ParseError as e:
                                log_tool_call(
                                    run_id=run_id,
                                    user_input=user_input,
                                    tool_name=item.name,
                                    arguments=json.dumps(args),
                                    tool_output=None,
                                    status="parse_error",
                                    error_message=str(e),
                                    final_response=None,
                                )
                                raise EPOUpstreamError("EPO returned malformed XML") from e
                            except ValueError as e:
                                patent_records = {"error": f"Invalid input for {item.name}: {str(e)}"}
                                status = "error"
                                error_message = str(e)
                            except EPOServiceError:
                                raise
                            except Exception as e:
                                log_tool_call(
                                    run_id=run_id,
                                    user_input=user_input,
                                    tool_name=item.name,
                                    arguments=json.dumps(args),
                                    tool_output=None,
                                    status="error",
                                    error_message=str(e),
                                    final_response=None,
                                )
                                raise AgentInternalError(f"Could not complete {item.name}") from e
                            else:
                                status = "success"
                                error_message = None
                            finally:
                                current_tool_call_id.reset(tool_call_id_token)
                                current_tool_name.reset(tool_name_token)
                                current_run_id.reset(run_id_token)
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
                tool_choice="none" if i == MAX_AGENT_ITERATIONS - 1 else "auto",
        )
        usage = response.usage
        if usage is not None:
            token_usage["input"] += usage.input_tokens
            token_usage["output"] += usage.output_tokens
        for item in response.output:
            input_list.append(cast(ResponseInputItemParam, item))
        i += 1
    if any(item.type == "function_call" for item in response.output):
        raise AgentRuntimeLimitError("Agent reached maximum iteration limit")
    final_response = response.output_text
    for log_id in logged_ids:
        update_final_response(log_id, final_response)
    if not actual_calls:    
        log_tool_call(run_id, user_input, tool_name=None, arguments=None, tool_output=None, status="no_tool_call", error_message=None, final_response=final_response)
    return actual_calls, tool_outputs, final_response
