# Good first issues
1. Add a `semester` filter (e.g. `20262`) to `list_courses` using the shortname hint. Files: `domain/courses.py`, tests.
2. Table-driven tests for `_type_ok` (assignment accepted file types: `.pdf`, `document`, `image`, `*`, mimetypes).
3. Extract rubric-looking tables from assignment statements HTML into a structured `criteria` list (`domain/assignments.py`).
4. Add `.odt/.ods/.odp` extraction (zip + XML, same approach as DOCX).
5. `doctor --json` redaction test with a malicious server response containing a token-like string.
6. Support `core_course_get_enrolled_courses_by_timeline_classification` as a faster "current courses" source.
7. Portuguese translation of `docs/clients.md` and `docs/auth.md`.
8. Add a Keycloak service to `compose.yaml` with a realm export for the OIDC dev setup.
9. Better quiz HTML support: `ordering`, `gapselect`; add parser tests with real-looking HTML snippets (synthetic).
10. Add Debian/Ubuntu instructions for the OCR extra.
