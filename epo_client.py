import os
import re
import requests
import xml.etree.ElementTree as ET
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from tenacity import (retry, retry_if_exception, stop_after_attempt, wait_exponential, wait_random, RetryCallState)
from errors import (AgentInternalError, EPOUpstreamError, EPORateLimitError,)
from logs_db import log_retry

EPO_TIMEOUT = (3.05, 10)
EPO_MAX_RETRIES = 2
EPO_BACKOFF_BASE_SECONDS = 0.5
epo_backoff_wait = wait_exponential(multiplier=EPO_BACKOFF_BASE_SECONDS) + wait_random(min=0, max=0.25)
current_run_id: ContextVar[str | None] = ContextVar("current_run_id", default=None)
current_tool_name: ContextVar[str | None] = ContextVar("current_tool_name", default=None)
current_tool_call_id: ContextVar[str | None] = ContextVar("current_tool_call_id", default=None)
epo_token = None
epo_token_expiry = None

ns = {"ex": "http://www.epo.org/exchange", "ops": "http://ops.epo.org"}

def is_retryable_epo_exception(exc) -> bool:
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        status_code = exc.response.status_code
        if status_code == 429 or (500 <= status_code < 600):
            return True
    return False

def wait_epo_retry(retry_state: RetryCallState) -> float:
    fallback_wait = epo_backoff_wait(retry_state)
    outcome = retry_state.outcome
    if outcome is None:
        return fallback_wait
    exc = outcome.exception()
    if exc is None:
        return fallback_wait
    if isinstance(exc, requests.exceptions.HTTPError):
        if exc.response is None:
            return fallback_wait
        status_code = exc.response.status_code
        if status_code == 429:
            retry_after = exc.response.headers.get("Retry-After")
            if retry_after is None:
                return fallback_wait
            try:
                retry_after_seconds = int(retry_after)
                if retry_after_seconds > 0:
                    return float(retry_after_seconds)
                return fallback_wait
            except ValueError:
                try:
                    retry_time = parsedate_to_datetime(retry_after)
                except ValueError:
                    return fallback_wait
                now = datetime.now(timezone.utc)
                wait_seconds = (retry_time - now).total_seconds()
                if wait_seconds > 0:
                    return wait_seconds
                return fallback_wait
    return fallback_wait

def log_epo_retry(retry_state: RetryCallState) -> None:
    attempt = retry_state.attempt_number
    outcome = retry_state.outcome
    if outcome is None:
        return
    exc = outcome.exception()
    if exc is None:
        return
    next_action = retry_state.next_action
    if next_action is None:
        return
    wait_seconds = next_action.sleep
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        reason = f"HTTP {exc.response.status_code}"
    else:
        reason = type(exc).__name__
    run_id = current_run_id.get()
    tool_name = current_tool_name.get()
    tool_call_id = current_tool_call_id.get()
    log_retry(run_id, tool_call_id, tool_name, "EPO", attempt, reason, wait_seconds)

@retry(
    stop=stop_after_attempt(EPO_MAX_RETRIES + 1),
    wait=wait_epo_retry,
    retry=retry_if_exception(is_retryable_epo_exception),
    reraise=True,
    before_sleep=log_epo_retry
)
def epo_request_with_retry(method, url, **kwargs) -> requests.Response:
    response = requests.request(method, url, **kwargs)
    response.raise_for_status()
    return response

def get_epo_access_token():
    global epo_token, epo_token_expiry
    consumer_key = os.getenv("EPO_CONSUMER_KEY")
    secret_consumer = os.getenv("EPO_CONSUMER_SECRET")
    if consumer_key is None:
        raise AgentInternalError("Missing EPO_CONSUMER_KEY in .env")
    if secret_consumer is None:
        raise AgentInternalError("Missing EPO_CONSUMER_SECRET in .env")
    if epo_token is not None and epo_token_expiry is not None and datetime.now() < epo_token_expiry:
        return epo_token
    else:
        try:
            r = epo_request_with_retry("POST", 'https://ops.epo.org/3.2/auth/accesstoken', auth=(consumer_key, secret_consumer), data={'grant_type': 'client_credentials'}, timeout=EPO_TIMEOUT)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in (401, 403):
                raise EPOUpstreamError("EPO authentication failed") from e
            if e.response.status_code == 429:
                raise EPORateLimitError("EPO rate limit exceeded") from e
            if  400 <= e.response.status_code < 500:
                raise EPOUpstreamError("EPO token request failed") from e
            raise
        try:
            data = r.json()
        except ValueError as e:
            raise EPOUpstreamError("EPO token response contains invalid JSON") from e
        if not isinstance(data, dict):
            raise EPOUpstreamError("EPO token response has invalid structure")
        try:
            epo_token = data["access_token"]
        except KeyError as e:
            raise EPOUpstreamError("EPO token response is missing access_token") from e
        if not isinstance(epo_token, str) or epo_token.strip() == "":
            raise EPOUpstreamError("EPO token response contains invalid access_token")
        now = datetime.now()
        try:
            expires_in = data["expires_in"]
            expires_in = int(expires_in)
        except KeyError as e:
            raise EPOUpstreamError("EPO token response is missing expires_in") from e
        except (ValueError, TypeError) as e:
            raise EPOUpstreamError("EPO token response contains invalid expires_in") from e
        epo_token_expiry = now + timedelta(seconds=expires_in-30)
        return epo_token

def get_epodoc_value(patent, container, child_tag):
    ref = patent.find(f'.//ex:{container}', ns)
    if ref is None:
        raise EPOUpstreamError(f"EPO response is missing {container}")
    doc_ids = ref.findall('ex:document-id', ns)
    for doc_id in doc_ids:
        if doc_id.get("document-id-type") == "epodoc":
            number = doc_id.find(f'ex:{child_tag}', ns)
            if number is None:
                raise EPOUpstreamError(f"EPO response is missing {child_tag}")
            if number.text is None:
                raise EPOUpstreamError(f"EPO response contains empty {child_tag}")
            return number.text
    raise EPOUpstreamError("No epodoc document-id found in EPO response")

def get_filtered_values(patent, element_tag, attribute_name, attribute_value, child_tag):
    matching_elements = patent.findall(f'.//ex:{element_tag}', ns)
    values = []
    for element in matching_elements:
        if element.get(attribute_name) == attribute_value:
            element_name = element.find(f'.//ex:{child_tag}', ns)
            if element_name is None:
                raise EPOUpstreamError(f"EPO response is missing {child_tag}")
            if element_name.text is None:
                raise EPOUpstreamError(f"EPO response contains empty {child_tag}")
            values.append(element_name.text)
    return values

def get_title(patent):
    title = None
    ref = patent.find(f'.//ex:bibliographic-data', ns)
    if ref is None:
        raise EPOUpstreamError("EPO response is missing bibliographic-data")
    titles = ref.findall('ex:invention-title', ns)
    for case in titles:
        if case.get("lang") == "en":
            title = case.text
    if title is None:
        raise EPOUpstreamError("EPO response does not contain an English invention title")
    return title

def get_applicants(patent):
    return get_filtered_values(patent, 'applicant', 'data-format', 'epodoc', 'name')

def search_patent(ti=None, pa=None, pn=None, ap=None, pd_from=None, pd_to=None, page=None):
    if any(value is not None and not isinstance(value, str) for value in [ti, pa, pn, ap, pd_from, pd_to]):
        raise ValueError("ti, pa, pn, ap, pd_from and pd_to must be strings or None")
    if all(value is None or value.strip() == "" for value in [ti, pa, pn, ap, pd_from, pd_to]):
        raise ValueError("At least one search criterion is required")
    for name, value in [("pd_from", pd_from), ("pd_to", pd_to)]:
        if value is not None:
            try:
                datetime.strptime(value, "%Y%m%d")
            except ValueError as e:
                raise ValueError(f"{name} must be a valid date in YYYYMMDD format") from e
    if (pd_from is not None and pd_to is not None) and (datetime.strptime(pd_from, "%Y%m%d") > datetime.strptime(pd_to, "%Y%m%d")):
        raise ValueError(f"pd_from ({pd_from}) cannot be after pd_to ({pd_to})")
    if page is not None and not isinstance(page, int):
        raise ValueError("Page must be an integer or None")
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
    try:
        r = epo_request_with_retry("GET", "https://ops.epo.org/rest-services/published-data/search", headers=headers, params={"q": query_string}, timeout=EPO_TIMEOUT)
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            fault_root = ET.fromstring(e.response.text)
            code = fault_root.find("ops:code", ns)
            if code is not None and code.text == "SERVER.EntityNotFound":
                return {
                    "results": [],
                    "total_results": 0,
                    "page": page,
                    "total_pages": 0,
                    "available_pages": 0,
                    "truncated": False,
                }
        raise
    root = ET.fromstring(r.text)
    search_info = root.find('.//ops:biblio-search', ns)
    if search_info is None:
        raise EPOUpstreamError("EPO response is missing biblio-search")
    result_count = search_info.get("total-result-count")
    if result_count is None:
        raise EPOUpstreamError("EPO response is missing total-result-count")
    try:
        result_count = int(result_count)
    except ValueError as e:
        raise EPOUpstreamError("EPO response contains invalid total-result-count") from e
    total_pages = (result_count + 24) // 25
    available_pages = min(total_pages, 80)
    truncated = result_count > 2000
    results = root.findall('.//ops:publication-reference', ns)
    publications = []
    for result in results:
        country = result.find('.//ex:country', ns)
        number = result.find('.//ex:doc-number', ns)
        kind = result.find('.//ex:kind', ns)
        if country is None or number is None or kind is None:
            raise EPOUpstreamError("EPO response contains incomplete publication reference")
        if country.text is None or number.text is None or kind.text is None:
            raise EPOUpstreamError("EPO response contains empty publication reference field")
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
    if pn is not None and not isinstance(pn, str):
        raise ValueError("Publication number must be a string or None")
    if pn is None or pn.strip() == "":
        raise ValueError("Publication number cannot be empty")
    normalized_pn = re.sub(r"[\s.\-_/]", "", pn.upper())
    match = re.fullmatch(r"([A-Z]{2})(\d+)([A-Z]\d?)?", normalized_pn)
    if match is None:
        raise ValueError("Unsupported publication number format")
    country, number, kind = match.groups()
    epodoc_pn = f"{country}{number}"
    details = {}
    token = get_epo_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    r = epo_request_with_retry("GET", f"https://ops.epo.org/rest-services/published-data/publication/epodoc/{epodoc_pn}/biblio", headers=headers, timeout=EPO_TIMEOUT)
    root = ET.fromstring(r.text)
    documents = root.findall('.//ex:exchange-document', ns)
    patent = None
    if kind is not None:
        for doc in documents:
            if doc.get("kind") == kind:
                patent = doc
                break
    else:
        latest_date = None
        for doc in documents:
            publication_date = get_epodoc_value(doc, "publication-reference", "date")
            if latest_date is None or publication_date > latest_date:
                latest_date = publication_date
                patent = doc
    if patent is None:
        raise EPOUpstreamError("EPO response did not contain requested publication data")
    details["publication_number"] = get_epodoc_value(patent, "publication-reference", "doc-number")
    details["filing_date"] = get_epodoc_value(patent, "application-reference", "date")
    details["title"] = get_title(patent)
    details["applicants"] = get_applicants(patent)
    return details
