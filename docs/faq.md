# Frequently asked questions

## Is it safe to give the server my Moodle token?

Treat a Moodle token like a password: it grants the permissions of its associated
web service and account. Enter it through `moodle-academic-mcp configure`, not in
an AI chat, issue, screenshot, or repository. Local credentials use the OS keyring
when available, or a permissions-restricted file; hosted credentials are stored
encrypted. You can revoke a token in Moodle and delete local data. See
[Tokens and VPN](tokens.md) and [Privacy](privacy.md).

## Does the AI model see my token?

The integration is designed to keep tokens out of tool results, logs, and model
prompts. The model can still receive **Moodle content returned by tools**, such as
course details, grades, or extracted documents. Use a model provider you trust
with that information. See [Privacy](privacy.md).

## What data leaves my computer?

The server contacts your configured Moodle host for requested operations. Tool
results are then processed by your chosen AI client/model provider, including
when the Moodle connector itself runs locally. Hosted deployments also process
data on the host you or your administrator operate. The integration has no
telemetry. See the storage and retention details in [Privacy](privacy.md).

## Will it work from off campus without a VPN?

That depends on your institution's current network policy. Some Moodle servers
require an approved VPN for certain operations. Run
`moodle-academic-mcp doctor` to diagnose connection failures; this project
does not bypass VPN, SSO, or institutional restrictions. See
[Tokens and VPN](tokens.md).

## Can it submit an assignment or change something in Moodle?

Yes, **write tools exist** for actions such as uploading and submitting work.
Their use depends on the permissions of your token and the AI client's tool
approval policy. Read-only browsing does not itself authorize a submission.
Review the requested operation and configure your client to require the
confirmations you want. The project does not impose a universal confirmation
policy; see [Tools](tools.md) and the [Feature matrix](feature-matrix.md).

## Does a successful mock test mean submissions are safe on my university's Moodle?

No. Mock tests exercise the project's own fake Moodle service, not an actual
institution's permissions, plugins, or workflow. Submission write paths may be
implemented and mock-tested without having been exercised on real Moodle.
Check each feature's evidence in the [Feature matrix](feature-matrix.md).

## What does “validated” mean?

`implemented` means code and tests exist; `documented` means a function is
described in a catalog; `detected` means an account's Moodle service reports
it. `validated_sandbox` or `validated_ufsc` specifically means the operation
was run against a real Moodle environment. Validation applies to the listed
operation and environment, not every university or every possible write.
See the [Feature matrix](feature-matrix.md).

## What happens to downloaded materials, saved indexes, and activity records?

Material indexes, snapshots, attendance records, and write journals can persist
in the configured local or hosted database. Hosted uploaded artifacts have a
separate default retention period. You can inspect the documented export and
purge options under [Privacy](privacy.md); do not assume a file disappears just
because a chat ends.

## Why can't I install it directly from PyPI?

The project is not published on PyPI yet. Follow the Git-based installation
instructions in the [README](../README.md), and see
[Distribution](distribution.md) for the publication process. Avoid installing
unrelated packages with similar names.
