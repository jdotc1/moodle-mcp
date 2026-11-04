# Roadmap (concrete gaps)

1. **Real-sandbox validation of all writes** (assignment delivery, quiz, groups, forum) — blocks any `validated_sandbox` claim. See `sandbox.md`.
2. Attendance: confirm what a *student* token can read (`mod_attendance_get_session` `attendance_log` visibility); implement the "session without roll call vs cancelled" mapping per site; UFSC sets may use different status names.
3. Group choice on UFSC: the mobile service export has no `mod_choicegroup_*`; discover whether another service exposes it or whether the course uses `groupselect`; optional browser fallback (user's own session, opt-in).
4. Quiz: file-upload questions (draft area `attachments`), drag-and-drop/ordering types, timer display, preflight (password/IP) rules, review after finishing.
5. Modules: Workshop, Lesson, Feedback, H5P, Choice results — dedicated tools (only generic read access today).
6. Rubrics/marking guides for students (`core_grading_*` is not in the UFSC mobile export; parse from statement/feedback for now).
7. OCR adapter tests; embeddings/RAG as an optional extra.
8. OIDC: test against Keycloak in Compose; refresh-token UX; rate limiting beyond onboarding.
9. Course filter by semester in `list_courses`; better "current course" detection (UFSC `enddate` is often 0).
10. PyPI release workflow (trusted publishing) once validated.
