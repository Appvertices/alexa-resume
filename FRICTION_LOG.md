# Friction log

Problems we hit while building Resume for the Alexa+ track, in the format the hackathon asks for.

## 1. Official MCP Python SDK 2.x renamed client APIs

| | |
|---|---|
| **Task** | Verify our self-hosted MCP server with the official MCP Python SDK. |
| **Steps** | Installed `mcp` 2.3.0 in a clean virtual environment and ran `tests/check_official_sdk.py`. |
| **Expected** | The client API used in existing examples (`McpError`, `result.protocolVersion`, `isError`, three values from `streamable_http_client`) to work. |
| **Actual** | `from mcp.shared.exceptions import McpError` failed (now `MCPError`). Result fields moved from camelCase to snake_case (`protocol_version`, `is_error`). `streamable_http_client` now yields two streams instead of three. |
| **Severity** | Medium. It blocked interoperability testing until fixed. |
| **Workaround** | Compatibility shims in `tests/check_official_sdk.py`, so the check runs on SDK 1.x and 2.x. |
| **Suggestion** | Publish a 1.x-to-2.x migration note listing renamed client APIs and changed return shapes, and keep deprecated aliases for one release. |

## 2. Alexa+ add-on tools are not available to participants

| | |
|---|---|
| **Task** | Test our MCP server with Alexa+ tooling (Category SDK, MCP Toolkit, CLI or Web Simulator). |
| **Steps** | Followed the Alexa+ add-on setup pages from the hackathon resources. |
| **Expected** | Access to a simulator or test console that can call a self-hosted MCP server. |
| **Actual** | The tools are preview-only for select partners. Some setup pages don't say so, and only the event FAQ made it clear. |
| **Severity** | Medium. We could not test against a real Alexa+ client. |
| **Workaround** | Built our own web MCP client (the FAQ's accepted path) and verified the server with the official MCP SDK. |
| **Suggestion** | Offer a public Alexa+ MCP test console or simulator for hackathons, and add a "partner-only" notice to every add-on setup page. |

## 3. JSON-only Streamable HTTP servers are under-documented

| | |
|---|---|
| **Task** | Implement Streamable HTTP with plain JSON responses and no SSE stream, using only the Python standard library. |
| **Steps** | Read the 2025-11-25 transport specification and built `POST /mcp` with JSON responses. |
| **Expected** | An end-to-end example of a JSON-only server: what `GET /mcp` should return, and how session and protocol-version headers behave. |
| **Actual** | We had to infer the details. We chose `405` with an `Allow` header for `GET /mcp` and require `MCP-Session-Id` after `initialize`. |
| **Severity** | Low. |
| **Workaround** | Wrote wire-level regression tests (`tests/test_protocol_regressions.py`) for each decision. |
| **Suggestion** | Add a minimal JSON-only Streamable HTTP server example to the MCP docs, covering `GET 405`, `DELETE` and the header rules. |

## 4. Windows reset the connection when the server rejected a request early

| | |
|---|---|
| **Task** | Reject requests with a bad `Accept` or `Content-Type` header (406/415) in the standard-library server. |
| **Steps** | Returned the error before reading the request body, then ran the test suite on Windows. |
| **Expected** | The client receives the 406/415 response. |
| **Actual** | Sometimes `ConnectionAbortedError` (WinError 10053): closing a socket with unread data resets the connection on Windows before the client reads the response. |
| **Severity** | Low. It caused an intermittent test failure. |
| **Workaround** | Read the bounded request body before rejecting headers (`server.py`). |
| **Suggestion** | Note this behaviour in the Python `http.server` documentation for early error responses. |
