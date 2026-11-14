# INF1103 Phase 2 — Engineering report working draft

**Project:** DoNotStress, team 1. **Purpose:** This is the implementation-grounded report content for the team's maximum **six-page** engineering report. Condense typesetting as needed. Complete the provenance/Git/TeamAId fields using real project evidence, not fabricated commits or tasks.

## 1. Solution and data flow

The system is an AI-assisted university student check-in, with a Flask GUI and admin-only history. It is non-diagnostic. The 11-item structured survey remains the same as Phase 1. Its runtime configuration/validation now lives in `SurveyDefinition`; `survey.py` retains only constants and compatibility adapters. Gemini processes **every evaluated check-in** and must return valid, schema-checked structured JSON; deterministic Logic independently checks the arithmetic and rule outcomes. No logic-only fallback can become an evaluated record. A failed AI request produces a retry response and may store a **pending** (unevaluated) row for later re-submission. Student identifiers and reflection text are excluded from AI prompts. Reflection text is not stored in JSON. See `docs/DATA_FLOW.mmd`.

**Pipeline:** Browser → `WebController` → `IOManager.validate_student_form` → `CheckinService.process_checkin` → `AIManager` → `AIProvider`/`GeminiProvider` → schema validation → `LogicManager`/`StressScorer`/`BusinessRule` → `DataManager`/`StorageBackend`/`JSONStorage` → allow-listed student result. Admin: session authorization → `DataManager.load_all_records` → records/tier/cohort filter and `CohortReport` → `/api/records` and `/api/summary` → admin UI.

## 2. Class design and required OOP mapping

For the main class diagram, use `docs/UML_CORE.puml` (relationships and multiplicities). The comprehensive, generated class/member map is `docs/UML_ALL_CLASSES.puml` (production `.py` classes with attributes and methods); a rendering of the architecture is `docs/OOP_OVERVIEW.png`. The code is authoritative: regenerate UML after changing any class.

| Mandatory concept | Real implementation and justification |
|---|---|
| Classes/objects | `create_app()` constructs `IOManager`, `LogicManager`, `DataManager`, `CheckinService`, `WebController`. Per-request `AIManager` is injected into the pipeline. |
| Encapsulation | `AIManager.__provider`, `DataManager.__backend`, `CheckinService.__logic` and other private fields; exposed read-only interfaces. |
| Abstract classes | `BaseManager(ABC)` requires `describe`; `AIProvider`, `Scorer` and `BusinessRule` define abstract operations. |
| Inheritance | All four managers subclass `BaseManager`; `GeminiProvider`/`FunctionProvider` subclass `AIProvider`; rule and scoring subclasses specialise contracts. |
| Interfaces/contracts | `AIProvider.generate`, `StorageBackend.load/append/replace`, and `BusinessRule.run` used through their base types. |
| Association | `CheckinService` references injected manager instances without owning their lifecycle; `LogicManager` works with externally created trend/history types. |
| Aggregation | `AIManager` accepts a provider created outside the manager; `LogicManager` accepts independently testable `BusinessRule` objects. |
| Composition | `LogicManager` creates the default `StressScorer` and a new `Decision` for each evaluation; default `CheckinService` creates its reporter. |
| Polymorphism | `AIManager` uses a shared `AIProvider` while `GeminiProvider` and `FunctionProvider` behave differently. `ResponseContract` runs `ResponseValidator` subclasses with no field-by-field branch in `AIManager`. `DataManager` works with `JSONStorage` or an in-memory backend. Logic loops through `BusinessRule.run` without type-specific branches. |

**Four functional manager responsibilities:** `IOManager` handles input validation and user formatting; `AIManager` handles prompt, provider calls, schema validation and retry; `LogicManager` handles deterministic scoring, cross-checking and composable rules; `DataManager` handles opt-in checks, privacy filtering, persistence and query/filtering. `WebController` only owns HTTP sessions, security, routing and presentation adapters. `CheckinService` coordinates managers and returns a student-safe view. `main.py` is the composition root; its four old direct-call helpers remain thin compatibility adapters for existing tests only and are **not** called by the HTTP runtime.

## 3. SOLID justification with evidence

- **S — Single Responsibility:** `WebController` receives transport requests, `IOManager` validates user values, `AIManager` owns AI contract/retry, `LogicManager` finalises decisions, `DataManager` persists and retrieves data, `CohortReport` computes aggregates. Changes to the AI provider do not require changing the domain scoring formula.
- **O — Open/Closed:** New Gemini alternatives implement `AIProvider`; new persistence engines implement `StorageBackend`; new business rules subclass `BusinessRule` and are injected. The current managers require no source edits for these substitutions.
- **L — Liskov Substitution:** `FakeProvider` and `GeminiProvider` must provide the same `AIProvider` operations; `JSONStorage` and the test `IsolatedMemoryStorage` meet the same `StorageBackend` contract. The offline tests demonstrate substituting these implementations without changing `AIManager` or `DataManager`.
- **I/D (supplemental):** Focused interfaces separate persistence from AI; constructor injection makes the domain independent of vendor SDK details and easy to test.

## 4. New useful feature and advanced feature

**New feature: admin cohort overview.** The original saved-record search is extended by an admin-only aggregate report accessible on the Saved Records page. `CohortReport.build()` computes total, evaluated, pending, mean stress of valid evaluated results, risk-tier counts and per-cohort statistics. It excludes reflection text, student IDs and AI reasoning from the JSON response. `CheckinService.summarize_records`, `WebController.records_summary` (`GET /api/summary`), and `static/campus.js` implement the path. Tests: `test_oop_architecture.py` covers aggregates, invalid data and privacy; `test_admin_summary.py` covers authorization, HTTP response and corrupt-file handling (requires Flask).

**Advanced feature: GUI.** `templates/`, `static/campus.js` and Flask `WebController` provide an interactive multi-step survey, student result panels and protected admin history. It is built on the existing Phase 1/Phase 2 application; its eligibility as a *newly introduced* advanced feature should be confirmed against the Phase 1 submission. Tests: `test_frontend.py`, `test_admin_access.py`, `test_records.py` and `test_duplicate_pending.py` (requires Flask).

## 5. Failure handling and tests

See `docs/EXCEPTION_MATRIX.md` for the full exception matrix. AI timeouts, quota errors, schema failures and empty keys return structured, sanitized failure codes instead of fabricated successful assessments. JSON writes are atomic; corrupt files receive timestamped backups. Unknown JSON dictionary formats and mixed list entries are backed up and rejected **without overwriting the original**. Failed writes return errors and the UI offers retry; invalid forms return field-level validation errors. Admin endpoints require server-side admin sessions and restrict data exposure. The application runs in one process; separate multi-worker deployments require shared server state, storage locking and stronger credential management.

**Automated tests:** `python -m unittest discover -s tests -v`. Offline unit tests use `FunctionProvider`/`FakeProvider` and sample JSON, not the live Google SDK. GitHub Actions `.github/workflows/tests.yml` installs pinned dependencies and runs the suite on push/PR. `Dockerfile` installs dependencies and runs the Flask application. **Verification completed in the supplied environment:** Python syntax compilation, JavaScript syntax check and **76 selected offline tests passed**. Complete Flask integration tests, a successful Docker build, actual CI status and live Gemini invocation have **not** been verified in this environment because Flask/Google SDK and Docker were unavailable. Do not claim these are verified without executing them.

## 6. Before final submission — actual evidence to attach

- **Git URL, branch and commit SHA:** [TEAM TO SUPPLY]. Upload this refactor in meaningful staged commits (e.g., `refactor: inject DataManager backend`, `refactor: move Flask routes to WebController`, `test: cover AI pipeline & storage schema failures`, `feat: add cohort overview`). Do **not** recreate false history.
- **TeamAId CSV and peer evaluation:** [TEAM TO EXPORT]. Must show genuine dated task ownership and progress; cannot be generated from source code.
- **Docker and full test output:** [TEAM TO RUN AND CAPTURE]. Run `docker build -t donotstress-phase2 .` then `docker run --rm -e GEMINI_API_KEY= -p 5000:5000 donotstress-phase2` to verify the UI, and `docker run --rm donotstress-phase2 python -m unittest discover -s tests -v` for test verification.
- **Live AI flow:** [TEAM TO TEST WITH OWN KEY]. Set `GEMINI_API_KEY` securely; submit an invented *synthetic* student check-in and verify that a real provider response is schema validated. Never commit the key or actual student responses.
- **Report:** export/trim this content into a **maximum six-page PDF**, keeping UML, data-flow diagram, exception matrix, OOP mapping, SOL justification and feature descriptions. Submit TeamAId CSV separately. Use the required lab-group/team ZIP naming, and submit by **18 November 2026, 23:59 Singapore time**.

**Academic honesty:** Engineering evidence, test results, Git commits and TeamAId chronology must be genuine. The uploaded archive alone cannot prove the team's historical contributions or that the feature was new relative to Phase 1.
