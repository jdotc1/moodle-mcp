# Feature matrix

States: **implemented** (code + tests) · **documented** (function schema in a catalog) · **detected** (`core_webservice_get_site_info` lists it for the token) · **validated_sandbox** / **validated_ufsc** (run against a real Moodle) · **unavailable** · **unknown**. Run `moodle-academic-mcp capabilities` for *your* account. Machine-readable evidence: `src/moodle_academic_mcp/data/validation.json`.

"Mock tests" use `tests/fake_moodle.py` and are **not** integration evidence.

| Feature | Implemented | Tests | Schema source | Real Moodle |
| --- | --- | --- | --- | --- |
| Courses, contents | yes | mock | UFSC wsdoc | **validated_ufsc (read, 2026-11-04, Moodle 4.1.5+)** |
| Assignments: statement, status (individual/group) | yes | mock | UFSC wsdoc | **validated_ufsc (read)** |
| Assignment delivery: upload → draft → submit, statement, read-back | yes | mock incl. lost-response reconciliation, idempotency | UFSC wsdoc + Moodle `upload.php` | **not run** (writes) |
| Deadlines (assign + quiz + calendar, windows, cutoff vs due, extension) | yes | mock + pure window tests | UFSC wsdoc | **validated_ufsc (read)**; note: calendar `instance` = cmid on this site |
| Quizzes: list/info/start/answer/finish | yes | mock (radio, text; parser for checkbox/select/textarea) | UFSC wsdoc | **not run**; question types in HTML-form protocol: multichoice, truefalse, shortanswer, numerical, essay-text, match (select) *expected*, drag-drop/JS types flagged unsupported |
| Forums/announcements read; post/reply | yes | mock | UFSC wsdoc | list of forums read **validated_ufsc**; discussions/posts read and writes not run |
| `changes_since` (snapshot diff) | yes | mock | — | not run |
| Grades read; what-if math | yes | mock + 12 pure tests | UFSC wsdoc | not run (needs a course with grades) |
| Attendance math (weights, pending, cancelled, scenario) | yes | pure tests incl. spec examples (72h/75%/10h → limit 18, margin 8; 30h present → 24 needed) | — | n/a |
| Attendance data from Moodle | adapter | mock (identity refusal) | upstream mod_attendance 4.x source | **not validated**; student-facing visibility of `attendance_log` unknown; needs identity proof |
| Manual attendance import | yes | yes | — | n/a |
| Group listing (core) | yes | mock | UFSC wsdoc | not run |
| Group choice join/leave | adapter | mock (full group, already member) | upstream mod_choicegroup source | **not validated**; functions absent from the UFSC mobile export |
| Group self-selection (`mod_groupselect`) | **unavailable** | — | no upstream WS | returns activity link |
| Choice (`mod_choice`) | yes | — | UFSC wsdoc | not run; documented as a vote, not membership |
| VPL | adapter | — | upstream mod_vpl source | **not validated**; separate token |
| Materials: PDF/DOCX/PPTX/HTML/text extraction, page refs, search | yes | yes (generated files) | — | not run on real files; scanned PDFs → `needs_ocr`; OCR adapter exists, untested |
| Generic invoker (read / write) | yes | yes | catalogs (407 functions) | n/a |
| Workshop, Lesson, H5P, Feedback | **gap** | — | read functions exist in the catalog (e.g. `mod_lesson_get_user_grade`) | only reachable via the generic invoker; see roadmap |
| Hosted: dev bearer, onboarding, encrypted creds, artifacts, OpenAPI | yes | real MCP client over HTTP, PostgreSQL 16 | — | n/a |
| Hosted: OIDC JWT verification | implemented | not exercised with a real IdP | — | **unvalidated** |
| CAGR / other UFSC systems | **not implemented, by design** | | | Moodle access does not grant access to them |
