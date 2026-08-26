# Patent Research Agent

A patent research agent built on the EPO OPS API and raw OpenAI function calling, without an agent framework. Three tools — two EPO OPS calls and one local computation — plus a tool-calling loop, SQLite logging of every tool call, and an eval harness that checks which tools the model chooses for a given natural-language question.

## Status

**Work in progress.** This is an in-development learning project, not a finished tool. The sections below separate what is implemented from what is not.

Implemented:

- OAuth2 client-credentials flow against EPO OPS, with the token cached in memory and refreshed 30 seconds before expiry
- `search_patent` — CQL query built dynamically from any combination of title, applicant, publication number, application number, and a publication date range
- `get_patent_details` — bibliographic data for one publication, parsed from OPS XML
- `expiration_date` — local calculation, no API call
- Agent loop that chains tools across turns (e.g. `get_patent_details` → `expiration_date`) without the order being prompted
- Per-tool `try`/`except`: a failing tool returns an error object to the model as a normal `function_call_output` instead of crashing the run
- SQLite logging of every tool call, grouped by `run_id`
- Eval harness: 11 cases, including pagination and one negative case where no tool should be called
- Interactive REPL (`run_repl()`) as the default mode — conversation history persists across turns in a session; `N` starts a new conversation (new `run_id`, cleared history), `E` exits
- Exception-type discrimination: network/HTTP errors (`requests.exceptions.RequestException`, surfaced via `raise_for_status()`), XML parsing errors (`ET.ParseError`), and a generic fallback are logged with distinct `status` values (`network_error`, `parse_error`, `error`)
- Full typing of conversation history using the OpenAI SDK `ResponseInputParam` / `ResponseInputItemParam` types
- Reusable XML helper for attribute-filtered list extraction; `get_applicants` now delegates to the generalised parser
- Paginated `search_patent` results: 25 records per page via `X-OPS-Range`, with total result count, theoretical page count, accessible page count, and explicit truncation metadata for the OPS 2,000-record retrieval limit

Not implemented (see [Planned](#planned)):

- Open-ended date ranges via CQL relational operators (handled in Python instead, see [Limitations](#limitations))
- Final-response evaluation; the current eval harness checks tool selection and arguments only

## Tools

| Tool | Type | Parameters | Returns |
| --- | --- | --- | --- |
| `search_patent` | EPO OPS (published-data search) | `ti`, `pa`, `pn`, `ap`, `pd_from`, `pd_to`, `page` — all optional | Up to 25 publication numbers plus `total_results`, `page`, `total_pages`, `available_pages`, and `truncated` |
| `get_patent_details` | EPO OPS (published-data biblio) | `pn` — required | `publication_number`, `filing_date`, `title`, `applicants` |
| `expiration_date` | Local computation | `filing_date` — required | Filing date + 20 years, `YYYYMMDD` |

`expiration_date` is deliberately a local, non-API tool, so that the model has to choose between *kinds* of tools rather than between similar API wrappers.

All three schemas use `"strict": true`, which requires every property to be listed in `required`; optionality is expressed by allowing `null` alongside the parameter's actual type.

## Requirements

- Python 3.12+ (tested on 3.14)
- OpenAI API key
- EPO OPS consumer key and secret (free registration at the [EPO developer portal](https://developers.epo.org/))

## Tech Stack

- Python
- OpenAI Responses API (`gpt-5.6-luna`), raw function calling — no agent framework
- EPO OPS 3.2 REST API (OAuth2 client credentials, CQL search)
- `requests`
- `xml.etree.ElementTree` (standard library — chosen over `lxml`, since only a handful of fields are read)
- SQLite3

## Installation

1. Clone the repository:

    ```bash
    git clone https://github.com/skykamil/patent-agent.git
    cd patent-agent
    ```

2. Install dependencies:

    ```bash
    pip install -r requirements.txt
    ```

3. Create a `.env` file with:

    ```
    OPENAI_API_KEY=your-key-here
    EPO_CONSUMER_KEY=your-epo-key-here
    EPO_CONSUMER_SECRET=your-epo-secret-here
    ```

## Usage

Running the script starts an interactive REPL:

```bash
python patent_agent.py
```

Each prompt shows three options:

```
N - New chat
E - Exit
How can I help you?
```

Type a natural-language question to have the agent answer it. Conversation history persists across turns within a session, so follow-up questions can refer to a previous answer without repeating details (e.g. asking "when will it expire?" after already looking up a patent's filing date). `N` clears the history and starts a new `run_id` for logging; `E` exits the program.

To run the eval harness instead of the REPL:

```bash
python patent_agent.py --eval
```

This executes the fixed 11-case eval set and prints a final score (e.g. `11/11`).

## Evaluation

The eval set contains 11 cases covering each tool individually, a two-tool chain, open-ended and bounded date ranges, pagination, and one negative case (`"What is 2 + 2?"`) where no tool should be called.

A case passes only if the *entire* sequence matches: the number of calls, the tool names in order, and the expected arguments as a subset of the actual ones (`expected.items() <= actual.items()`, which tolerates the `null` values forced by `"strict": true`).

Last verified: **11/11.**

The harness scores *tool selection only*. It does not check whether the data returned by EPO OPS is correct, nor whether the model's final text answer is accurate.

## Logging

Every tool call is written to `agent_logs` in `logs_db.db`:

| Column | Description |
| --- | --- |
| `id` | Autoincrement primary key |
| `run_id` | UUID4, shared by all calls within one REPL conversation (until `N` starts a new one) or one eval case |
| `timestamp` | ISO 8601, local time |
| `user_input` | The original natural-language question |
| `tool_name` | Which tool was called |
| `arguments` | Arguments the model supplied, as a JSON string |
| `tool_output` | What the tool returned, as a JSON string |
| `status` | `success`, `network_error`, `parse_error`, `error`, or `no_tool_call` |
| `error_message` | Exception text, `NULL` on success |
| `final_response` | The model's final text answer for that turn, as a plain string |

`run_id` makes it possible to reconstruct a multi-step chain after the fact — for example `get_patent_details` followed by `expiration_date`, sharing one `run_id` across two rows. Each row also carries the model's final response for that turn — even when a tool was called, so the row shows both the tool call and the text the model ultimately gave the user.

## Project Structure

| File | Description |
| --- | --- |
| `patent_agent.py` | Tool schemas, EPO OPS client, XML parsing, agent loop, eval set |
| `logs_db.py` | SQLite schema, tool-call logging, and final-response updates |
| `requirements.txt` | Python dependencies |
| `.gitignore` | Excludes `.env`, `*.db`, and `__pycache__/` from the repo |
| `logs_db.db` | SQLite log file, created on first run (not tracked in the repo) |

## Planned

- Open-ended date ranges through CQL relational operators, if Appendix 4.2 of the OPS reference guide (CQL index catalogue) can be reached — it was not retrievable through the documentation route used so far
- Evaluation of the agent's final text response in addition to the existing tool-selection eval

## Out of Scope

Deliberately excluded from this project: integration with commercial patent/IP management platforms, multi-agent orchestration, agent frameworks (LangChain and similar), patent lifecycle documents beyond A1/B1 (A2, B2 and so on), and OPS services other than published-data search and biblio (images, fulltext, family, register, legal, classification, number-service).

## License

MIT — see [LICENSE](LICENSE).

## Limitations

**`expiration_date` is a 20-year arithmetic calculation, nothing more.** It adds 20 years to the filing date and returns the result. It does not account for supplementary protection certificates (SPCs), patent term extensions or adjustments, terminal disclaimers, renewal fee status, or early termination through withdrawal, lapse, revocation, or opposition. The function name promises considerably more than the implementation delivers. The output is not a reliable expiry date for any real patent and must not be relied on for any legal or docketing purpose.

Other known limitations:

- `search_patent` returns publication numbers only — no titles, applicants, or dates. Enriching results requires a separate `get_patent_details` call per number, which the tool description explicitly discourages the model from doing automatically.
- `search_patent` returns 25 records per page. OPS exposes the total hit count but allows retrieval of only the first 2,000 records from a result set, so at most 80 pages are accessible. Broader searches must be narrowed to reach records beyond that limit.
- `get_patent_details` selects the B1 document if present, otherwise A1. Any other kind code is ignored; if neither is present, parsing fails and the failure surfaces as a caught tool error rather than a result.
- Open-ended date ranges are a workaround in Python, not CQL. `pd_from` alone is expanded to a range ending at today's date, meaning the same query can produce different results on different days; `pd_to` alone is expanded to a range starting at the hardcoded constant `19000101`.
- The agent loop is hard-capped at three iterations. When the cap is hit, the loop simply stops — the user is not told the answer may be incomplete.
- Within a REPL session, `input_list` grows with every turn and is never trimmed or summarized — long conversations mean larger, costlier prompts on each turn. History resets only on `N` (new conversation) or when the script exits; there is no persistence across separate runs of the script.
- Exception handling distinguishes network/HTTP errors and XML parsing errors from other failures via `status`, but everything else (e.g. missing/malformed data after a successful parse) still falls into the generic `error` status.
- A throttled response (HTTP 429) is caught the same way as any other HTTP error and logged with `status="network_error"` — there is no dedicated detection, backoff, or retry logic specific to rate limiting.
- The CQL syntax used here was verified empirically against live requests rather than derived from the full documentation. It works for the tested combinations, but is not guaranteed to cover the operators or index names described in the parts of the reference guide that were not reachable.
- The current eval score is 11/11, but model output is non-deterministic; treat the score as directional.
- There are no unit tests. The eval set is the only automated check, and it covers tool selection, not data correctness.
- `logs_db.db` is created relative to the current working directory, so running the script from different directories produces separate log databases.