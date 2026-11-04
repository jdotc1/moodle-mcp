# Moodle sandbox for write tests

Writes (delivery, quiz attempts, groups, forum posts) must be tested **only** on a development Moodle with synthetic data — never on real courses. The automated suite uses a mock; to earn `validated_sandbox` you need this.

**Status: not executed by the author.** The recipe below is untested; fix it and send a PR with the evidence.

1. Run Moodle 4.1–4.5 + MariaDB (e.g. `docker compose -f sandbox/compose.yaml up -d`; image names change over time, check Bitnami/Moodle HQ docs).
2. Enable web services and REST, create the external service "Moodle mobile web service" (built in) and enable it; install optional plugins `mod_attendance`, `mod_choicegroup`, `mod_vpl` if you want to test those adapters.
3. Create 2 students, 1 teacher, a course with: an assignment (files+text, drafts + statement), a group assignment, a quiz (multichoice, truefalse, shortanswer, essay), a Group choice activity with capacity, a forum, and gradebook items.
4. Tokens: `/user/managetoken.php` as each student → `configure --profile sandbox --url http://localhost:8081 --allow-insecure-http --allow-private-networks`.
5. Run the manual checklist in `sandbox/CHECKLIST.md`, then record each passed feature in `validation.json` (`validated_sandbox`) with date/version.
