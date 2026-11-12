# Codex live readiness — complete

Date: 2026-11-01. Code/readiness executor: Codex `w4:pH`; live operations coordinator: Codex `w4:pZ`.

## Outcome

Console uses actual Hermes data from exactly `/home/duy/2026.1/hermes-agent/.runtime/hermes-home`. No blocking backend code defect was found in the live path. One requested product wording correction is complete: non-demo session/overview badges say **Live data**, and the runtime-history workspace says **Hermes workspace**. Session mode remains `standalone`; the label does not claim that a gateway is always connected.

Files changed in this readiness task:
- `src/hermes_zalo/admin_static/console.js`: topbar, workspace and overview labels.
- `deploy/vercel-admin/tests/console.test.js`: regression for live labels with/without runtime history, unchanged standalone mode and retained demo labels.
- This report. All previous Round 2 and unrelated dirty working-tree changes were preserved.

## Direct read-only checks by w4:pH

Loaded repo `.env`, and subsequently the coordinator-created private connection environment, only inside the probe process; no values were logged. Probed AssistantAdmin, WorkspaceAdmin, RuntimeHistory and all registered sections: activity, analytics, benchmarks, briefing, connectors, experiments, operations and reports. No external API/model call, outbound chat, service operation or real configuration/auth mutation was performed.

- Explicit Console `--home` selection wins over an unrelated inherited HERMES_HOME; application workspace and registered-section context resolve to the exact target home. Telemetry resolves to that home's assistant-core directory. No personal data-home fallback was used.
- Before startup, runtime DB had 1 session and 41 message rows; 28 user/assistant messages are displayable. Tool/system/internal rows are intentionally excluded. History was available and matched those counts without logging IDs or content.
- Existing real telemetry/observability and 58 skills were available. Missing cron/jobs.json and console/kpis.json were not created. Reports and up-next stayed empty; Home showed only measured internal metrics. No fake social KPI, cron or report was inserted.
- Before the coordinator configured the local API connection, Chat correctly reported unavailable/not configured; stopped gateway and stale scheduler were detected from repo-home state. Starting Telegram alone is not the Web Chat prerequisite: the Console also needs its configured API endpoint/key.
- After the coordinator started services and installed the private connection environment, independent service probes showed Chat configured/available/enabled, gateway running, scheduler fresh and API health OK. Runtime history then contained 2 sessions. API health probing was restricted to loopback; there were no model POSTs by this executor.
- Actual status and model execution are separate: WorkspaceAdmin.chat_status validates configuration without sending a request. Operations health checks the configured local health endpoint. Mocked Chat tests verify execution/error behavior separately.

Probe artifacts: `/tmp/hermes-live-probe.py`, `/tmp/hermes-live-probe.log` (before startup), `/tmp/hermes-live-connected.log` (after startup). Logs contain counts/status/field names, not credentials, config bodies, usernames, chat IDs or message contents.

## Coordinator-provided live evidence

The following was executed and reported by w4:pZ, not by this executor's mocked tests:

- Created and started project Console and gateway services using the target repo home; local Console health 8787, API health 8642 and authenticated model listing returned HTTP 200.
- Completed exactly one real model-only POST through `/api/assistant/chat`, with no tools: expected reply marker returned with `source=gateway-api` in 6.26 seconds. No Telegram/Zalo test message was sent.
- Restarted only the project Console after the label change. Served JS contains Live data / Hermes workspace while session mode remains standalone.
- Final actual snapshots: **2 sessions, 26 usage rows, 10 activity turns, 58 skills**; Reports and Automations empty as expected. Gateway running with two connected platforms (Telegram/API server), scheduler fresh, API health OK.
- Personal gateway remained untouched. Docker daemon and sandbox image were reported ready; this executor did not execute a tool/sandbox task.

Coordinator evidence artifact: `.runtime/console-live/live-status.json`. Private connection/auth artifacts were not copied into this report or printed.

## Validation performed here

| Check | Actual result | Log |
| --- | --- | --- |
| Targeted Python modules below | **119 tests OK**, 1.314s | `/tmp/hermes-live-tests.log` |
| `node --test tests/console.test.js` in deploy/vercel-admin | **52/52 pass**, 0 fail/skip | `/tmp/hermes-live-js-tests.log` |
| `git diff --check` | Pass | local check and coordinator confirmation |

Python command:

```text
PYTHONPATH=src:. .runtime/venv/bin/python -m unittest tests.test_admin_assistant tests.test_admin_workspace tests.test_admin_runtime_history.RuntimeHistoryTests tests.test_admin_runtime_history.StandaloneConsoleCliTests.test_paths_do_not_fall_back_to_personal_hermes tests.test_admin_observe tests.test_admin_briefing tests.test_admin_operations -q
```

Chat execution tests use `httpx.MockTransport`; operations controls use a fake runner. No standalone CLI server-process integration test was rerun under this task's no-process-control constraint. No broad/full-suite rerun was necessary for this label-only change; the prior Round 2 full gate remains documented separately.

## Limits and remaining work

No remaining code/readiness task items. No visual browser verification is claimed in this live run: the coordinator reported CUA unavailable. Evidence here covers direct services, tests, HTTP/auth/static asset checks and the coordinator's real model E2E.

External platform KPI collection, scheduled reports, sandbox tools and outbound Telegram/Zalo delivery were not exercised. Their current absence is represented honestly in the Console. Future usage/activity changes depend on real gateway traffic and the assistant-core telemetry plugin. No deploy, commit, push, service start/stop, recipient change or real secrets/auth modification was performed by w4:pH.
