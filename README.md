# Patent Research Agent

A patent research agent built on the EPO OPS API and raw OpenAI function calling, without an agent framework. Three tools — two EPO OPS calls and one local computation — plus a tool-calling loop, SQLite logging of every tool call, and an eval harness that checks both tool-call behavior and the agent's final response.

## Status

**Core agent complete — v1.0. Productionization in progress.** This is a learning project and prototype, not a production or legal-status tool. Version 1.0 closes the core CLI agent: EPO OPS search and bibliographic lookup, local patent-term calculation, multi-step tool use, logging, pagination, typed conversation history, and deterministic evaluation of both tool calls and final responses.

Current development focuses on productionizing the existing agent rather than expanding its patent-domain capabilities. The first productionization layer adds a FastAPI HTTP interface and persistent multi-turn conversation state in SQLite.

### Core v1.0

- OAuth2 client-credentials flow against EPO OPS, with the token cached in memory and refreshed 30 seconds before expiry
- `search_patent` — CQL query built dynamically from any combination of title, applicant, publication number, application number, and a publication date range
- `get_patent_details` — bibliographic data for one publication, parsed from OPS XML
- `expiration_date` — local calculation, no API call
- Agent loop that chains tools within a turn (e.g. `get_patent_details` → `expiration_date`) without the order being prompted
- Per-tool `try`/`except`: a failing tool returns an error object to the model as a normal `function_call_output` instead of crashing the run
- SQLite logging of every tool call, grouped by `run_id`
- Eval harness: 11 cases covering tool selection, pagination, date-range behavior, a two-tool chain, a no-tool case, and deterministic checks of the final response
- Interactive REPL (`run_repl()`) as the default mode — conversation history persists across turns in a session; `N` starts a new conversation (new `run_id`, cleared history), `E` exits
- Exception-type discrimination: network/HTTP errors (`requests.exceptions.RequestException`, surfaced via `raise_for_status()`), XML parsing errors (`ET.ParseError`), and a generic fallback are logged with distinct `status` values (`network_error`, `parse_error`, `error`)
- Full typing of conversation history using the OpenAI SDK `ResponseInputParam` / `ResponseInputItemParam` types
- Reusable XML helper for attribute-filtered list extraction; `get_applicants` now delegates to the generalised parser
- Paginated `search_patent` results: 25 records per page via `X-OPS-Range`, with total result count, theoretical page count, accessible page count, and explicit truncation metadata for the OPS 2,000-record retrieval limit
- Final-response evaluation: static checks for stable known answers plus dynamic checks against the actual `search_patent` output, including every returned publication number, pagination metadata, and the OPS retrieval-limit notice when applicable

### Productionization after v1.0

- FastAPI HTTP interface with `POST /chat` and a simple `GET /` health endpoint
- Pydantic request and response models for the chat API
- Automatic OpenAPI / Swagger UI documentation
- FastAPI lifespan initialization for SQLite
- `conversation_id`-based multi-turn API conversations
- Persistent conversation history in SQLite, surviving application restarts
- Responses API history serialization into a JSON-safe, replayable representation before persistence

## Tools

| Tool | Type | Parameters | Returns |
| --- | --- | --- | --- |
| `search_patent` | EPO OPS (published-data search) | `ti`, `pa`, `pn`, `ap`, `pd_from`, `pd_to`, `page` — all optional | Up to 25 publication numbers plus `total_results`, `page`, `total_pages`, `available_pages`, and `truncated` |
| `get_patent_details` | EPO OPS (published-data biblio) | `pn` — required | `publication_number`, `filing_date`, `title`, `applicants` |
| `expiration_date` | Local computation | `filing_date` — required | Simplified filing date + 20 years, `YYYYMMDD` |

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
- FastAPI
- Pydantic
- Uvicorn

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

This executes the fixed 11-case eval set and prints separate tool-call and final-response scores.

### HTTP API

Start the development server:

```bash
uvicorn api:app --reload
```

Interactive OpenAPI documentation and request testing are available at:

```text
http://127.0.0.1:8000/docs
```

Start a new conversation by sending a request without a `conversation_id`:

```json
{
  "message": "Get details for publication number EP1000000"
}
```

The response contains the agent answer and a generated conversation ID:

```json
{
  "answer": "...",
  "conversation_id": "..."
}
```

To continue the same conversation, send the returned ID with the next request:

```json
{
  "message": "When will it expire?",
  "conversation_id": "..."
}
```

Conversation history is persisted in SQLite and can be restored after the API process restarts.

## Evaluation

The eval set contains 11 cases covering each tool individually, a two-tool chain, open-ended and bounded date ranges, pagination, and one negative case (`"What is 2 + 2?"`) where no tool should be called.

### Tool-call evaluation

A case passes only if the *entire* expected call sequence matches: the number of calls, the tool names in order, and the expected arguments as a subset of the actual ones (`expected.items() <= actual.items()`, which tolerates the `null` values forced by `"strict": true`).

### Final-response evaluation

The final response is checked deterministically rather than with an LLM judge.

For stable cases, the harness checks required response content. For `search_patent`, it validates the answer against the actual tool output from that run: every returned publication number must be present, the reported page must match `page X of Y`, the total result count and accessible page count must appear, and truncated result sets must mention the 2,000-record OPS retrieval limit.

The expiry case additionally requires language making clear that the calculated date is simplified and not a verified legal expiration date.

Last verified on **2026-08-26**:

- **Tool-call eval: 11/11**
- **Final-response eval: 11/11**

The harness does not independently verify that EPO OPS data itself is correct, and it is not a legal-status validator. It checks whether the agent selected the expected tools and whether its final answer reflects the returned tool data and required caveats.

## Logging and Conversation Persistence

Every tool call is written to `agent_logs` in `logs_db.db`:

| Column | Description |
| --- | --- |
| `id` | Autoincrement primary key |
| `run_id` | UUID4 used to group related tool-call log rows. In the REPL it identifies the whole conversation until `N` starts a new one; in the FastAPI layer a new `run_id` is created for each `POST /chat` request. |
| `timestamp` | ISO 8601, local time |
| `user_input` | The original natural-language question |
| `tool_name` | Which tool was called |
| `arguments` | Arguments the model supplied, as a JSON string |
| `tool_output` | What the tool returned, as a JSON string |
| `status` | `success`, `network_error`, `parse_error`, `error`, or `no_tool_call` |
| `error_message` | Exception text, `NULL` on success |
| `final_response` | The model's final text answer for that turn, as a plain string |

`run_id` makes it possible to reconstruct a multi-step chain after the fact — for example `get_patent_details` followed by `expiration_date`, sharing one `run_id` across two rows. Each row also carries the model's final response for that turn — even when a tool was called, so the row shows both the tool call and the text the model ultimately gave the user.

API conversation state is tracked separately through `conversation_id` in the `conversations` table. `agent_logs` does not yet store `conversation_id`, so conversation-level tracing across multiple HTTP requests is not yet available.

Persistent API conversation state is stored in the `conversations` table:

| Column | Description |
| --- | --- |
| `conversation_id` | Primary key identifying one multi-turn API conversation |
| `history` | Serialized Responses API conversation history stored as JSON text |
| `created_at` | ISO 8601 timestamp set when the conversation is first stored |
| `updated_at` | ISO 8601 timestamp refreshed when the conversation history is updated |

## Project Structure

| File | Description |
| --- | --- |
| `patent_agent.py` | Tool schemas, EPO OPS client, XML parsing, agent loop, eval set |
| `api.py` | FastAPI application, request/response models, conversation handling, and history serialization |
| `logs_db.py` | SQLite schema, tool-call logging, final-response updates, and persistent conversation storage |
| `requirements.txt` | Python dependencies |
| `.gitignore` | Excludes `.env`, `*.db`, and `__pycache__/` from the repo |
| `logs_db.db` | SQLite database for agent logs and persistent conversation history, created on first run (not tracked in the repo) |


## Next

Version 1.0 remains the frozen core agent milestone. Current work focuses on productionizing the application rather than expanding the patent-domain feature set:

- API error handling and HTTP status mapping
- Runtime safeguards, limits, and timeouts
- Retry/backoff behavior for external API failures and rate limits
- API and persistence tests
- Docker and deployment
- Observability for errors, latency, and token usage
- Further separation of API, agent, persistence, and domain layers

## Out of Scope

Deliberately excluded from this project: integration with commercial patent/IP management platforms, multi-agent orchestration, agent frameworks (LangChain and similar), patent lifecycle documents beyond A1/B1 (A2, B2 and so on), and OPS services other than published-data search and biblio (images, fulltext, family, register, legal, classification, number-service).

## License

MIT — see [LICENSE](LICENSE).

## Limitations

**`expiration_date` is a simplified 20-year arithmetic calculation, nothing more.** It adds 20 years to the filing date and returns the result. It does not account for supplementary protection certificates (SPCs), patent term extensions or adjustments, terminal disclaimers, renewal fee status, or early termination through withdrawal, lapse, revocation, or opposition. The agent is explicitly instructed to present the result as a simplified filing-date-plus-20-years calculation, not as a verified legal expiration date. The output must not be relied on for legal or docketing purposes.

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
- The last verified eval scores are 11/11 for tool calls and 11/11 for final responses, but model output is non-deterministic; treat the scores as directional rather than as a guarantee.
- There are no unit tests. The eval set is the only automated check; it covers tool behavior and final-response completeness/contract checks, not the independent correctness of EPO OPS data.
- `logs_db.db` is created relative to the current working directory, so running the script from different directories produces separate log databases.
- API conversation history is persisted as JSON in SQLite. The serializer is intentionally tailored to the Responses API item types currently used by this agent rather than being a general-purpose Responses API serializer.
- Assistant history serialization currently assumes the relevant text is the first content item in the returned message.
- `POST /chat` creates a new `run_id` for each agent execution while `conversation_id` identifies the multi-turn conversation. `agent_logs` does not yet store `conversation_id`.
- The API currently has no authentication, authorization, concurrency safeguards, or production deployment configuration.
- Persisted API conversation history currently grows without trimming, summarization, expiration, or cleanup. Long-running conversations therefore increase both stored history size and the amount of context sent to the model.
