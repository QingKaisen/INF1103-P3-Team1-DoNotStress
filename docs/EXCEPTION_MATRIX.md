# Phase 2 — exception-handling matrix

| Failure | Owner | Handling | Outcome / test |
|---|---|---|---|
| Missing `GEMINI_API_KEY` | `AIManager` / `GeminiProvider` | Check `is_ready()` before live call; no fake AI result | `missing_api_key`, pending path; `test_ai_manager_oop.py` |
| Gemini timeout | `AIManager` | Retry/fallback within overall four-provider-call limit; sanitized failure | `timeout`/`retries_exhausted`, `test_ai_manager.py` |
| Rate limiting (429) | `AIManager` | Move to next configured model without uncontrolled retry | `rate_limited`, `test_ai_manager.py` |
| Connection/provider failure | `AIManager` | Classify exceptions without logging secrets | `unavailable`, `test_ai_manager_oop.py` |
| AI returns malformed JSON or schema | `AIManager` | Parse and validate all fields; reject/fallback | `invalid_response`, `test_response_validators.py` |
| AI score disagrees with survey score | `LogicManager` / `ScoreAuditor` | Audit multi-condition AI result, reject instead of substituting Logic-only value | pending/retry; `test_logic_manager.py` |
| Invalid student ID or survey response | `IOManager` | Validate field/range/required answers, return named errors | HTTP 400, `test_frontend.py` |
| Missing JSON data file | `JSONStorage` | Read as empty list | HTTP 200 empty admin table; `test_storage.py` |
| Malformed existing JSON | `JSONStorage` | Back up untouched original; reading raises, save may start fresh *only after successful backup* | Admin HTTP 500 when loading, backups; `test_storage.py` |
| JSON object has unknown structure | `JSONStorage` | Back up then refuse save, preserve original | `StorageError`, `test_oop_architecture.py` |
| JSON list contains non-object elements | `JSONStorage` | Back up then refuse save, preserve all original bytes | `StorageError`, `test_oop_architecture.py` |
| File permission/disk/atomic write issue | `JSONStorage`/`DataManager` | Temporary file + `os.replace`, cleanup; return error | HTTP 500/retry; `test_storage.py` |
| Unsupported or missing opt-in | `DataManager` | Refuse saving | error response; `test_oop_architecture.py` |
| Unauthorized admin history or summary | `WebController` | Server-session check before data read | HTTP 401 / redirect; `test_admin_summary.py` |
| Oversized form payload | `WebController` | Flask max payload + error handler | HTTP 413, `test_frontend.py` |
| Lost/invalid CSRF token | `WebController` | Constant-time token comparison for POST | HTTP 403, `test_admin_access.py` |

All health guidance is non-diagnostic; a pending check-in is never presented as an AI-evaluated outcome.
