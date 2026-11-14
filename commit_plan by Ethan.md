# DoNotStress — 19-class commit plan

**For the team.** This is a plan only. No code is in this file. Each step below is one branch, one pull request, one squash merge, with the tests green before it is merged.

- **Today:** Friday 13 November 2026, late evening (SGT).
- **Deadline:** Wednesday 18 November 2026, 23:59 SGT.
- **Starting point:** graded repo `main` at `d2450f5`, plus open **PR #39** (must merge first).
- **End point:** exactly **19 production classes**, Flask UI kept, behaviour of the current app kept, plus the student history page and background check-in jobs.

Python 3.12. After every commit, on Windows PowerShell:

```powershell
python -m unittest discover -s tests 2>&1 | Select-Object -Last 4
```

Expect `OK`. The number of tests may change slightly as class-shape tests are dropped and history and threading tests are added. It starts at **196**.

---

## How to read this plan

The running app on `main` is still the Part 1 pipeline: `ai_manager.py`, `logic_manager.py`, `data_manager.py`, `io_manager.py` and `main.py` are **functions, not classes**. The 41 classes already on `main` live in `ai/`, `logic/`, `questions/`, `records/` and `storage/`, and are covered by unit tests, but **the Flask path does not import them**. `data_manager` has its own file I/O and does not use `JSONStorage`. `survey.py` has its own question data and does not use `Questionnaire`.

That is useful. The early commits can delete helper classes and retarget their tests without changing what students see. The later "turn this manager into a class" commits are the ones that wire the remaining classes into the running app. Those are the ones to review more carefully.

Do **not** replace the graded repo with a trimmed 19-class folder. That reference app shows the target shape (method names, who owns what), but it has only 20 tests and would throw away the 196-test suite. Copy only the files listed for that commit, then run the full suite.

Do **not** merge `feature-io-manager-class`, or the closed pull requests from earlier in the month.

Leave `STEPS.md`, patches and this plan out of the repo. Commit only application and test files. Do not mention tools or other project nicknames in commits, pull requests, comments or the report.

`print()` and `input()` stay only in `io_manager.py`. The Flask path does not call them. Keep a `create_app()` factory function so existing tests and `flask --app main:app` still work. Do not construct a live `App` (with a thread pool) at import time.

If a day is slipping, commits marked **can be combined** may go in one pull request, as long as every class that is deleted has its callers updated in that same commit. Do not combine across packages (for example Logic with Data) — review and ownership get messy.

---

## Starting classes on `main` (`d2450f5`)

Counted with Python's `ast` module, production code only. Test doubles (`FakeProvider` once it is in `tests/`, `MemoryStorage`, `unittest.TestCase` subclasses) are not counted.

**41 production classes** on `main`. After PR #39, **40**.

| Package | Classes (file:line) |
|---|---|
| `ai/` (17, then 16 after PR #39) | `AIFailure` (`ai_failure.py:6`), `AIProvider` (`ai_provider.py:10`), `AIResult` (`ai_result.py:9`), `ErrorClassifier` (`error_classifier.py:8`), `FakeProvider` (`fake_provider.py:15`, moved out by PR #39), `GeminiProvider` (`gemini_provider.py:13`), `ScoreAuditor` (`score_auditor.py:10`), and the 11 validators: `ResponseValidator`, `RangeValidator`, `ChoiceValidator`, `IdListValidator`, `ReasoningValidator`, `ScoredItemsValidator`, `ScoreValidator`, `ConfidenceValidator`, `SoftLabelValidator`, `StressorsValidator`, `TipsValidator` |
| Root (1) | `BaseManager` (`base_manager.py:16`) |
| `logic/` (11) | `BusinessRule`, `ContextFlagRule`, `Decision`, `SafetyOverrideRule`, `ScoreCard`, `StressScorer`, `SupportPriorityRule`, `TipCapRule`, `Trend`, `TrendAnalyser` |
| `questions/` (4) | `Question`, `Questionnaire`, `SafetyCheck`, `Section` |
| `records/` (4) | `Record`, `CheckInRecord`, `PendingRecord`, `StressHistory` |
| `storage/` (4) | `JSONStorage`, `SaveResult`, `StorageBackend`, `StorageError` |

There is **no** `App`, **no** `WebController`, **no** `AIManager` / `LogicManager` / `DataManager` / `IOManager` class, **no** history page, and **no** background job pool. `process_checkin()` in `main.py` is synchronous: the HTTP request waits for Gemini. `main.py` already has an `RLock` around pending-record bookkeeping. `JSONStorage` already has an `RLock` and atomic writes, but the running app does not use it.

### Target — 19 production classes

`App`, `BaseManager`, `WebController`, `IOManager`, `Questionnaire`, `AIManager`, `AIProvider`, `GeminiProvider`, `LogicManager`, `BusinessRule`, `SafetyOverrideRule`, `SupportPriorityRule`, `ContextFlagRule`, `Decision`, `DataManager`, `Record`, `CheckInRecord`, `PendingRecord`, `JsonStorage`.

`FakeProvider` stays in `tests/` and is not counted.

**27 classes are folded or deleted. 6 classes are added** (`LogicManager`, `DataManager`, `AIManager`, `IOManager`, `App`, `WebController`). `JSONStorage` is kept and renamed `JsonStorage`. The other 12 already-kept classes stay.

---

## Suggested calendar

| Day | Commits | Owner |
|---|---|---|
| Fri 13 Nov (tonight) | **1** — merge PR #39 once CI is green | Ethan |
| Sat 14 Nov | **2–5** — slim Logic, then `LogicManager` | Ethan commits; Zhung Yi and Qing Kai review |
| Sun 15 Nov | **6–8** — slim Data, then `DataManager`. **9** — slim AI results | Xiang He on 6–8 (Ethan can prepare the files). Ethan on 9; Bernice reviews |
| Mon 16 Nov | **10–11** — slim AI validators, then `AIManager`. **12–13** — slim I/O, then `IOManager` | Ethan on 10–11; Bernice reviews. Glenn on 12–13 (Ethan can prepare the files) |
| Tue 17 Nov | **14, 15, 16** — `App` and `WebController`, then threading, then the history page | Ethan |
| Wed 18 Nov | No class changes. Report, UML of all 19 classes, CI, Docker, TeamAid, final zip. Submit before 23:59 SGT. | Ethan drafts; team reviews the report |

Tuesday is the risky day: it adds the two features the brief asks for (one new user function, and the advanced threading feature). If Monday slips, start commit 14 as soon as the four manager classes exist, even if a last I/O tidy follows.

---

## Commit 1 — Move FakeProvider into tests (already on GitHub)

| | |
|---|---|
| **Branch** | `refactor/fake-provider-to-tests` |
| **Commit message** | `Move FakeProvider into tests so the app only ships real providers` |
| **Owner / day** | Ethan / Friday 13 November |
| **PR** | [#39](https://github.com/ethancheang/INF1103-P3-Team1-DoNotStress/pull/39) — merge this before starting commit 2 |
| **Deleted** | `ai/fake_provider.py` |
| **Added** | `tests/fake_provider.py` |
| **Changed** | `tests/test_providers.py` |
| **Classes gone** | `FakeProvider` leaves the application (it remains a test double) |
| **Classes appear** | none |
| **Class count after** | **40** (from 41) |
| **Risk** | Low. Already committed (`eb1aea9`) and pushed. Confirm CI is green, then squash-merge. |
| **Tests** | **196**, unchanged |

### Suggested PR title and body (for PR #39)

**Title:** Move FakeProvider into tests so the app only ships real providers

**Body:**

```
## What this does
Moves FakeProvider from ai/fake_provider.py into tests/fake_provider.py,
and points tests/test_providers.py at the new path.

FakeProvider is a test double. It should not ship as part of the
application, and it should not import GeminiProvider. After this change,
the only production implementor of AIProvider in ai/ is GeminiProvider.
The tests still use FakeProvider as a second implementor of the same
interface.

## Why
First step toward the 19-class design: the application only ships real
providers, and the test fake stays in tests/.

## How to review
- ai/fake_provider.py is deleted.
- tests/fake_provider.py is added, with no import of GeminiProvider.
- tests/test_providers.py only changes its import path.
- All 196 tests pass.

## Test plan
python -m unittest discover -s tests -v
Expect: Ran 196 tests ... OK
```

---

## Commit 2 — Fold TipCapRule into Decision

| | |
|---|---|
| **Branch** | `refactor/decision-finalise-tips` |
| **Commit message** | `Move tip capping onto Decision and drop TipCapRule` |
| **Owner / day** | Ethan / Saturday 14 November |
| **Deleted** | `logic/tip_cap_rule.py` |
| **Changed** | `logic/decision.py`, `tests/test_business_rules.py` |
| **Classes gone** | `TipCapRule` |
| **Classes appear** | none |
| **Class count after** | **39** |
| **Risk** | Low. `TipCapRule` is only used by its own tests, not by the running app. Smallest next commit — a good one to land first on Saturday. |
| **Tests** | **196**. Retarget `test_tip_cap_leads_keeps_gemini_tips_and_stops_at_four` so it calls `Decision.finalise_tips()` instead of `TipCapRule`. |

**What to do.** Add `Decision.finalise_tips()` with the current tip-cap behaviour (lead tips, then Gemini's tips, then extras, stop at four, no duplicates). Remove the public `tips` setter so a rule cannot overwrite the list directly. Rules keep using `lead_with()` and `suggest()`.

**Can be combined** with commits 3 and 4 if Saturday is tight.

---

## Commit 3 — Fold ScoreAuditor, StressScorer and ScoreCard

| | |
|---|---|
| **Branch** | `refactor/fold-scoring-helpers` |
| **Commit message** | `Fold scoring helpers into logic_manager and drop ScoreAuditor` |
| **Owner / day** | Ethan / Saturday 14 November |
| **Deleted** | `ai/score_auditor.py`, `logic/stress_scorer.py`, `logic/score_card.py` |
| **Changed** | `tests/test_stress_scorer.py` (retarget or remove if every behaviour test is already in `test_logic_manager.py`) |
| **Classes gone** | `ScoreAuditor`, `StressScorer`, `ScoreCard` |
| **Classes appear** | none |
| **Class count after** | **36** |
| **Risk** | Low for the running app (`logic_manager` already has `score_answers()`, `verify_ai_scores()` and `scoring_instructions()`). Medium if those two copies of the formula ever drifted — compare `StressScorer` with `logic_manager` before deleting, and keep `logic_manager` as the survivor. |
| **Tests** | **183–196**. Drop the two class-shape tests (`ScoreAuditor` cannot be constructed; `StressScorer` is a `ScoreAuditor`) and the `ScoreCard` holder test. The 11 behaviour tests already have twins in `test_logic_manager.py` (reverse scoring, sleep bands, 0.01 tolerance, `instructions()` text). If the whole file is a duplicate, deleting it leaves **183**. |

**What to do.** Do not drop `instructions()` from the Gemini prompt, and do not drop `verify()` (tier, scored_items, 0.01 tolerance) from the retry loop. Both already live on `logic_manager`. This commit only removes the unused parallel classes. `ScoreAuditor` sits under `ai/` but belongs to Logic — do not start an AI fold on that file until this is merged.

---

## Commit 4 — Fold Trend and TrendAnalyser

| | |
|---|---|
| **Branch** | `refactor/fold-trend` |
| **Commit message** | `Move trend analysis onto logic_manager and drop Trend classes` |
| **Owner / day** | Ethan / Saturday 14 November |
| **Deleted** | `logic/trend.py`, `logic/trend_analyser.py` |
| **Changed** | `logic_manager.py` (add a `trend()` function that takes a list of score dicts and returns a plain dict), `tests/test_trend.py` |
| **Classes gone** | `Trend`, `TrendAnalyser` |
| **Classes appear** | none |
| **Class count after** | **34** |
| **Risk** | Low. These classes are unused by the running app. This also removes a Logic-to-Records import (`TrendAnalyser` currently imports `StressHistory`). `trend()` must take dicts, not a history class, so I/O and Logic stay decoupled. |
| **Tests** | Same as after commit 3, still **2** trend tests, retargeted. **183–196**. |

The history page is not added yet. `trend()` just needs to exist and be tested, so commit 16 can call it.

---

## Commit 5 — Turn logic_manager into LogicManager

| | |
|---|---|
| **Branch** | `refactor/logic-manager-class` |
| **Commit message** | `Turn logic_manager into a LogicManager class that runs the business rules` |
| **Owner / day** | Ethan / Saturday 14 November (Sunday morning if commit 4 lands late) |
| **Deleted** | none |
| **Added** | none (same file, now a class) |
| **Changed** | `logic_manager.py`, `main.py`, and every test that calls module functions: `tests/test_logic_manager.py`, `tests/test_evidence_survey.py`, `tests/test_required_functions.py`, `tests/test_ai_manager.py`, `tests/test_repeatability.py`, and any other file that uses `logic_manager.evaluate` / `apply_logic` / `scoring_instructions` / `verify_ai_scores` |
| **Classes gone** | none |
| **Classes appear** | `LogicManager` (subclass of `BaseManager`) |
| **Class count after** | **35** |
| **Risk** | Medium. This is the first commit that changes the running pipeline. `evaluate()` must use `Decision` and the three policy rules (`SafetyOverrideRule`, `SupportPriorityRule`, `ContextFlagRule`) in a loop, then call `Decision.finalise_tips()`. Behaviour must match today's functions: same scores, same tips, same safety override, same 0.01 check. Keep `ContextFlagRule` even though it is thin — it is part of the open-closed evidence. |
| **Tests** | **183–196**, all retargeted at `LogicManager()`. Rename `scoring_instructions()` to `instructions()` and `verify_ai_scores()` to `verify()`, and update `main.py` so it passes **both** into the AI call. Keep `score()` as the formula only, and `evaluate()` as the full pipeline. |

This is a wider commit because every caller has to move in the same change. There is no remaining procedural `apply_logic()` wrapper. Ethan commits this himself, with no co-authors. Zhung Yi and Qing Kai review.

---

## Commit 6 — Fold storage helpers; keep JsonStorage

| | |
|---|---|
| **Branch** | `refactor/fold-storage-helpers` |
| **Commit message** | `Make JsonStorage standalone and drop the unused storage helpers` |
| **Owner / day** | Xiang He / Saturday–Sunday 14–15 November (can overlap Logic once PR #39 is merged; does not touch Logic files) |
| **Deleted** | `storage/storage_backend.py`, `storage/save_result.py`, `storage/storage_error.py`, `storage/__init__.py` |
| **Added** | `json_storage.py` at the repo root (moved and renamed from `storage/json_storage.py`) |
| **Changed** | the new `json_storage.py` (`JSONStorage(StorageBackend)` becomes a standalone `JsonStorage` with its own `RLock`, corrupt-file backup and atomic replace), `tests/test_storage.py` |
| **Classes gone** | `StorageBackend`, `SaveResult`, `StorageError` |
| **Classes appear** | none (`JSONStorage` continues as `JsonStorage`) |
| **Class count after** | **32** |
| **Risk** | Low for the running app (it still uses `data_manager`'s own I/O). Medium for the tests: keep the current load-vs-append contract. `load()` on a corrupt file raises and never pretends the file is empty. `append()` backs the corrupt file up and may start a fresh file. If the backup itself fails, the original file is left untouched. Raise `OSError` / `ValueError` instead of `StorageError`. |
| **Tests** | **181–194**. Drop `test_storage_backend_cannot_be_constructed` and `test_a_second_backend_can_implement_the_same_contract` (`MemoryStorage` goes away). Retarget the rest at `JsonStorage` on a temporary path. `SaveResult` assertions become a dict or tuple from the save call. Own this trade-off in the report: open-closed on storage is weaker, because a second backend would mean editing `DataManager`. |

The running `data_manager` is not switched over yet. That is commit 8. After this commit there is only one storage class, ready to be composed.

---

## Commit 7 — Fold StressHistory; put the three records in one file

| | |
|---|---|
| **Branch** | `refactor/fold-stress-history` |
| **Commit message** | `Move student history onto data_manager and keep the three record classes together` |
| **Owner / day** | Xiang He / Sunday 15 November |
| **Deleted** | `records/stress_history.py`, `records/check_in_record.py`, `records/pending_record.py` |
| **Changed** | `data_manager.py` (add `history_for(student_id)` that loads the file and filters in memory), `records/record.py` (move `CheckInRecord` and `PendingRecord` into this file, same behaviour), `records/__init__.py`, `tests/test_record_classes.py`, and any leftover `from records.check_in_record` / `from records.pending_record` imports |
| **Classes gone** | `StressHistory` |
| **Classes appear** | none (`Record`, `CheckInRecord`, `PendingRecord` stay, just in one file) |
| **Class count after** | **31** |
| **Risk** | Low. File moves plus a new function. In the report, say honestly that `history_for()` loads the whole file and filters in memory — do not claim it only reads that student's rows from disk. Keep LSP tests: reflection is never stored; `PendingRecord.score` is always `None`; both kinds go through the same save path. |
| **Tests** | **181–194**. Retarget the two `StressHistory` tests at `history_for()`. |

**Can be combined** with commit 6 if Xiang He prefers one Data tidy-up pull request (class count after the combined PR: **31**).

---

## Commit 8 — Turn data_manager into DataManager, composing JsonStorage

| | |
|---|---|
| **Branch** | `refactor/data-manager-class` |
| **Commit message** | `Turn data_manager into a DataManager class that owns JsonStorage` |
| **Owner / day** | Xiang He / Sunday 15 November |
| **Deleted** | the duplicated file-I/O functions inside `data_manager.py` (they move to calls on `JsonStorage`) |
| **Changed** | `data_manager.py`, `main.py`, `tests/test_storage.py`, `tests/test_records.py`, `tests/test_record_classes.py`, `tests/test_required_functions.py`, `tests/test_repeatability.py` |
| **Classes gone** | none |
| **Classes appear** | `DataManager` (subclass of `BaseManager`) |
| **Class count after** | **32** |
| **Risk** | Medium. Today the running app writes files through procedural `data_manager`, and `JsonStorage` is a parallel implementation. This commit must **switch** the running path over, not leave two writers. `DataManager(path)` constructs its own `JsonStorage` (composition). One `RLock` lives on `JsonStorage` only — do not add a second lock on `DataManager`. The application-level save lock stays on `App` later (commit 14 / 15). Pending-record replace, automatic save, and "refresh does not duplicate" must still pass. |
| **Tests** | **181–194**, retargeted at `DataManager` and a temporary file. No in-memory fake backend. |

---

## Commit 9 — Fold AIFailure, AIResult and ErrorClassifier

| | |
|---|---|
| **Branch** | `refactor/fold-ai-results` |
| **Commit message** | `Return AI outcomes as dicts and drop AIResult, AIFailure and ErrorClassifier` |
| **Owner / day** | Ethan / Sunday 15 November; Bernice reviews |
| **Deleted** | `ai/ai_failure.py`, `ai/ai_result.py`, `ai/error_classifier.py` |
| **Changed** | `tests/test_ai_result.py` (fold these 2 tests into `tests/test_ai_manager.py`, then delete the file), `ai_manager.py` only if a helper needs to move — the procedural module already returns dicts |
| **Classes gone** | `AIFailure`, `AIResult`, `ErrorClassifier` |
| **Classes appear** | none |
| **Class count after** | **29** |
| **Risk** | Low. The running AI module already uses dicts. Keep the error-code mapping (`unavailable`, rate-limit, invalid response, missing key) as a private function or, once commit 11 lands, a private method. Do not change what the student sees on a Gemini failure. |
| **Tests** | **181–194**. The two result tests stay as behaviour tests against the dict shape. |

**Can be combined** with commit 10.

---

## Commit 10 — Fold the 11 AI validators into config

| | |
|---|---|
| **Branch** | `refactor/fold-ai-validators` |
| **Commit message** | `Validate Gemini replies with config on ai_manager and drop the validator classes` |
| **Owner / day** | Ethan / Monday 16 November; Bernice reviews (her validator files are the ones being folded; those earlier commits stay in the git history) |
| **Deleted** | the whole `ai/validators/` package (11 class files plus `__init__.py`) |
| **Changed** | `ai_manager.py` (allow-lists and ranges that `build_prompt()` and `validate_response()` already use must keep the same numbers; copy any constant that today is only on a validator class), `tests/test_response_validators.py` |
| **Classes gone** | `ResponseValidator`, `RangeValidator`, `ChoiceValidator`, `IdListValidator`, `ReasoningValidator`, `ScoredItemsValidator`, `ScoreValidator`, `ConfidenceValidator`, `SoftLabelValidator`, `StressorsValidator`, `TipsValidator` |
| **Classes appear** | none |
| **Class count after** | **18** |
| **Risk** | Medium. A missed allow-list (soft labels, tip IDs, stressor IDs, score range) will change Gemini prompt text or accept a reply that used to be rejected. Compare each validator's constants with `ai_manager.py` before deleting. Open-closed is weaker here: a new *kind* of check means editing `validate_response()`. That is a deliberate trade-off for the report, not an accident. |
| **Tests** | **181–194**. Drop the "every validator shares the contract" shape test. Keep scored-items, range, choice, id-list, soft-label quotes, and reasoning tests against `validate_response()`. |

Many files are deleted, but they are one family. The edits that need care are `ai_manager.py` and the one test file.

---

## Commit 11 — Turn ai_manager into AIManager

| | |
|---|---|
| **Branch** | `refactor/ai-manager-class` |
| **Commit message** | `Turn ai_manager into an AIManager class that uses any AIProvider` |
| **Owner / day** | Ethan / Monday 16 November; Bernice reviews |
| **Deleted** | none |
| **Added** | `ai/model_chain.py` (the model fallback list, as data, so `FakeProvider` never imports `GeminiProvider`) |
| **Changed** | `ai_manager.py`, `ai/gemini_provider.py` (read the chain from `model_chain`, not a class attribute if that forces the fake to import it), `main.py`, `tests/test_ai_manager.py`, `tests/test_providers.py`, `tests/test_required_functions.py`, `tests/test_repeatability.py` |
| **Classes gone** | none |
| **Classes appear** | `AIManager` (subclass of `BaseManager`) |
| **Class count after** | **19** |
| **Risk** | Medium. `AIManager` is constructed with a provider, the questionnaire, `instructions` and `verify` from `LogicManager`. Model fallback, the call cap, "pending on failure", and "no logic fallback when Gemini fails" must all still hold. Tests inject `FakeProvider` and never call live Gemini. `GEMINI_API_KEY` stays empty in CI. |
| **Tests** | **181–194**, retargeted at `AIManager(...)`. |

After this commit the class count briefly sits at 19, then drops again when I/O helpers are folded, then returns to 19 when `App` and `WebController` are added. That mid-stream dip is expected. The UML for the report is drawn only against the final 19.

---

## Commit 12 — Fold Question, Section and SafetyCheck into Questionnaire

| | |
|---|---|
| **Branch** | `refactor/fold-questionnaire` |
| **Commit message** | `Fold Question, Section and SafetyCheck into Questionnaire` |
| **Owner / day** | Glenn / Monday 16 November (Ethan can prepare the files) |
| **Deleted** | `questions/question.py`, `questions/section.py`, `questions/safety_check.py` |
| **Added** | `questionnaire.py` at the repo root (moved from `questions/questionnaire.py`) |
| **Changed** | `questionnaire.py` (it now owns `validate_answer()`, `option_label()` and `safety_fired()`; callers get copies of question dicts, not a live list they can mutate), `survey.py` (keep the question *data*, add a `make_questionnaire()` function, do **not** leave a module-level `QUESTIONNAIRE` instance), `tests/test_questionnaire.py`. Delete `questions/` if it is empty. |
| **Classes gone** | `Question`, `Section`, `SafetyCheck` |
| **Classes appear** | none (`Questionnaire` stays) |
| **Class count after** | **16** |
| **Risk** | Medium. `survey.py` and `Questionnaire` are currently two parallel models, and the running app uses `survey.py`. This commit must make `make_questionnaire()` the single source the rest of the app will use, and must not change question wording, ranges, reverse-scored keys, or safety patterns. Keep the `option_label` tests. |
| **Tests** | **181–196**. Retarget the 3 questionnaire tests; `test_evidence_survey.py` should still pass. |

**Can be combined** with commit 13.

---

## Commit 13 — Turn io_manager into IOManager

| | |
|---|---|
| **Branch** | `refactor/io-manager-class` |
| **Commit message** | `Turn io_manager into an IOManager class that shapes every page` |
| **Owner / day** | Glenn / Monday 16 November |
| **Deleted** | none |
| **Changed** | `io_manager.py`, `main.py`, `tests/test_required_functions.py`, `tests/test_records.py`, `tests/test_frontend.py`, `tests/test_evidence_survey.py` if they call `io_manager.format_*` as functions |
| **Classes gone** | none |
| **Classes appear** | `IOManager` (subclass of `BaseManager`) |
| **Class count after** | **17** |
| **Risk** | Medium. Form validation and the student-facing copy must stay the same. Wording tables stay as module-level data, not new classes, so the file can be long without becoming a "god class" of many types. Add `display_history()` now (even with no route yet) so commit 16 only has to call it. `print()` and `input()` may remain as `display()` / `prompt_input()` in this file only, unused by Flask, so `test_display_functions_print` can be kept or rewritten against those methods. Do **not** merge the existing `feature-io-manager-class` branch — it is a different design. |
| **Tests** | **181–196**, retargeted at `IOManager`. A small `test_io_manager.py` for form validation and page data is welcome. |

`IOManager` and `AIManager` are handed the same `Questionnaire` instance (aggregation). `App` will create that instance in commit 14. Until then, `main.py` can call `make_questionnaire()` and pass it in.

---

## Commit 14 — Add App and WebController (still synchronous)

| | |
|---|---|
| **Branch** | `refactor/app-and-web-controller` |
| **Commit message** | `Add App as the composition root and move Flask routes to WebController` |
| **Owner / day** | Ethan / Tuesday 17 November (depends on commits 5, 8, 11 and 13) |
| **Deleted** | none |
| **Added** | `web_controller.py` |
| **Changed** | `main.py` (defines `class App` and a `create_app()` factory that builds `App` when called), every test that imports `create_app` or `process_checkin` or `result_view`: `tests/test_frontend.py`, `tests/test_admin_access.py`, `tests/test_duplicate_pending.py`, `tests/test_records.py`, `tests/test_ai_manager.py`, `tests/test_logic_manager.py`, `tests/test_repeatability.py` |
| **Classes gone** | none |
| **Classes appear** | `App`, `WebController` |
| **Class count after** | **19** |
| **Risk** | Medium–high, mostly from moving routes. Student-visible behaviour stays the same: check-in is still synchronous in this commit. `App` creates and owns the four managers and the questionnaire (via `make_questionnaire()`). `App` does **not** import `WebController`. `WebController` talks only to `App` use-case methods (`validate_form`, `process_checkin`, `admin_records`, and so on). It must not build a `CheckInRecord` itself, must not catch raw storage exceptions, and must not reach into `app.io`. Keep admin login, CSRF, automatic save, and "no reflection in storage" behaviour identical. |
| **Tests** | **181–196**. `create_app()` remains the entry the tests use. No live `App()` at module import. |

After this merge, the 19-class structure is in place. The two remaining commits add features, not classes.

---

## Commit 15 — Background check-in jobs

| | |
|---|---|
| **Branch** | `feature/background-checkin-jobs` |
| **Commit message** | `Run Gemini on a worker thread and let the browser poll for the result` |
| **Owner / day** | Ethan / Tuesday 17 November |
| **Deleted** | none |
| **Added** | `tests/test_threading.py` |
| **Changed** | `main.py` (`App` gains a `ThreadPoolExecutor`, `__save_lock`, job dicts that each hold a `threading.Event`, `submit_checkin()`, `job_status()`, `wait_for_job()`, `expire_jobs()`, `close()`), `web_controller.py` (POST `/` returns **202** and a `statusUrl`; GET `/checkin/status/<job_id>` polls; job IDs cannot be polled from another session; expire jobs on each request), `static/campus.js` (poll until done), `tests/test_frontend.py`, `tests/test_duplicate_pending.py`, and any other test that POSTs a check-in and currently expects an immediate finished result |
| **Classes gone / appear** | none |
| **Class count after** | **19** |
| **Risk** | High. This is the advanced feature, and it changes both the server and the browser. Existing frontend tests will fail until they poll. Do not lose these primitives: `App.__save_lock` around "match pending + save" (the file lock alone is not enough), a `threading.Event` in each job (not just a status string), job expiry so the map does not grow forever, and `App.close()` so tests and reloads do not leak workers. Duplicate in-flight submissions with the same fingerprint reuse the same job. |
| **Tests** | **191–206**. Add about **10** threading tests. Update the existing frontend and duplicate-pending tests so they follow the 202-then-poll path. All must still use `FakeProvider`. |

---

## Commit 16 — Student history page

| | |
|---|---|
| **Branch** | `feature/student-history` |
| **Commit message** | `Add a student history and trend page behind a signed cookie` |
| **Owner / day** | Ethan / Tuesday 17 November |
| **Deleted** | none |
| **Added** | `tests/test_history.py`; a history view in the existing campus page (the 19-class design uses a "My history" tab in `templates/checkin.html`, not a separate template) |
| **Changed** | `web_controller.py` (`GET /history`, `GET /api/history`, HMAC-signed cookie of digests, never the raw student ID, `MAX_IDS = 8`, `hmac.compare_digest`), `main.py` (`App.student_history()`), `static/campus.js`, `templates/checkin.html`, `templates/admin_login.html` (a "My history" link is fine), `tests/test_frontend.py` if navigation is asserted |
| **Classes gone / appear** | none |
| **Class count after** | **19** |
| **Risk** | Medium as a delivery risk (this is the new **user** function the brief requires — if Tuesday is rushed, this is what must not slip). Low as a class-design risk: `history_for()`, `trend()` and `display_history()` already exist from earlier commits. A student sees only their own rows, unlocked after a check-in on this browser. Admin can still see records through the existing admin path. |
| **Tests** | **207–222**. Add about **16** history tests (cookie grant, digest comparison, no raw IDs, rejected ID, admin bypass, trend wording). Final suite target: about **210–220**. |

---

## Running class count

| After commit | Production classes | What changed |
|---:|---:|---|
| `main` `d2450f5` | 41 | Starting point |
| 1 (PR #39) | 40 | `FakeProvider` → `tests/` |
| 2 | 39 | − `TipCapRule` |
| 3 | 36 | − `ScoreAuditor`, `StressScorer`, `ScoreCard` |
| 4 | 34 | − `Trend`, `TrendAnalyser` |
| 5 | 35 | + `LogicManager` |
| 6 | 32 | − `StorageBackend`, `SaveResult`, `StorageError` (`JSONStorage` kept as `JsonStorage`) |
| 7 | 31 | − `StressHistory` |
| 8 | 32 | + `DataManager` |
| 9 | 29 | − `AIFailure`, `AIResult`, `ErrorClassifier` |
| 10 | 18 | − 11 validators |
| 11 | 19 | + `AIManager` |
| 12 | 16 | − `Question`, `Section`, `SafetyCheck` |
| 13 | 17 | + `IOManager` |
| 14 | **19** | + `App`, `WebController` |
| 15 | **19** | Threading feature; no new class |
| 16 | **19** | History feature; no new class |

Wednesday does not change the count. The report's UML lists these 19 and no others.

---

## What each teammate owns

| Person | Area | Commits |
|---|---|---|
| Ethan | Logic (he types these commits, no co-authors), AI, App / web / features, and preparing files if a teammate is short on time | 1–5, 9–11, 14–16; prepare 6–8 and 12–13 if needed |
| Zhung Yi, Qing Kai | Logic review | Review 2–5 |
| Bernice | AI review (the validator work being folded is hers; those commits remain in the history) | Review 9–11 |
| Lam Xiang He | Data | 6, 7, 8 |
| Glenn | I/O | 12, 13 |

If a teammate cannot turn a branch around in time, Ethan opens the pull request from prepared files and that teammate reviews, so the area still has their name on it. For Logic, only Ethan commits.

---

## Things that can go wrong (read before starting)

1. **PR #39 has to land first.** Commit 2 is based on `main` after that squash merge. If CI on #39 is not green, fix that before anything else.

2. **History, `WebController` and background jobs are new work, not a tidy-up.** They do not exist on graded `main`. They are commits 14–16, they change `campus.js`, and they are the two features the brief asks for. Leave Tuesday for them. Do not spend Tuesday still folding validators.

3. **The 19-class reference app is not a drop-in.** It is a design guide (who owns which method, how `WebController` talks only to `App`, how `JsonStorage` is composed). Pasting it over the graded repo would replace 196 tests with 20 and drop CI assumptions. Each commit copies only the files named above.

4. **Two implementations of the same job exist today.** Scoring lives in both `logic_manager` and `StressScorer`. File I/O lives in both `data_manager` and `JSONStorage`. Questions live in both `survey.py` and `Questionnaire`. When a commit "folds" or "wires" one of these, the running path must end with a single implementation. Compare them before deleting, and keep the behaviour the current tests already lock down.

5. **Manager-class commits touch more than a handful of files**, because every caller moves in the same commit (`test_required_functions.py` in particular still calls procedural APIs). That is expected. The fold-only commits (2–4, 6–7, 9–10, 12) are the small ones.

6. **`test_frontend.py` and `test_duplicate_pending.py` will break in commit 15** until they poll. Budget time for that, not just for the new threading tests.

7. **Thread-safety is easy to drop.** When jobs become dicts, keep `threading.Event`, `App.__save_lock`, and `JsonStorage`'s `RLock`. Three different locks, three different jobs. `DataManager` does not get a second file lock.

8. **`ScoreAuditor` is under `ai/` but is a Logic fold (commit 3).** If AI and Logic work in parallel on Saturday, leave that file to Logic.

9. **Do not claim in the report** that history "only reads that student's rows" from disk, or that `Record` is an interface (it has shared code — draw solid inheritance), or that a method called `valid_to_save()` exists (the real method is `problem()`).

10. **Windows copy reminders.** Do not copy the whole folder. Do not commit this plan, a `.patch`, or a `STEPS.md`. After `git add` of the named files, run `git status` and check that only those files appear. Line-ending warnings (`LF` vs `CRLF`) can be ignored. Use `Select-Object -Last 4` to read the test summary.

---

## Wednesday wrap-up (no class changes)

- Six-page report, with a UML of all **19** classes and the correct arrow types (solid hollow triangle for inheritance, dashed hollow triangle for interfaces, filled diamond for composition, hollow diamond for aggregation, solid arrow for association).
- A short table from each required OOP concept to the class that shows it.
- A before-and-after against Part 1 (commit `6262aeb`, 14 October 2026: four procedural manager files, no classes).
- Honest paragraphs on the validator and storage trade-offs, on `history_for()` loading the whole file, and on efficiency (threading, locked atomic JSON).
- CI green on `main`, a working Docker build, the TeamAid sheet, and the final zip, uploaded before 23:59 SGT.
