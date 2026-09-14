from datetime import datetime
from openai.types.responses.function_tool_param import FunctionToolParam

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

def expiration_date(filing_date):
    if not isinstance(filing_date, str):
        raise ValueError("Filing date must be a string")
    if filing_date.strip() == "":
        raise ValueError("Filing date cannot be empty")
    parsed = datetime.strptime(filing_date, "%Y%m%d")
    expiration = parsed.replace(year=parsed.year + 20)
    return expiration.strftime("%Y%m%d")