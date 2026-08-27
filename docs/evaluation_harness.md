# Evaluation Harness

The included `examples/scripted_demo.py` is a deterministic integration demo, not a
SWE-bench result. It uses the production `AgentLoop` with an OpenAI response-shaped scripted
model so the full local-tool and verification path can be replayed without an API key.

## Task set

Start with the included calculator repair task. Add further tasks as isolated fixture
repositories, each with a bug description, an executable regression test, and an expected
post-repair condition. Keep test fixtures separate from the agent implementation.

## Metrics

| Metric | Definition |
|---|---|
| Task success rate | Tasks that reach `complete` with valid current-revision evidence / all tasks. |
| Verification coverage | Edited tasks that run at least one clean verification command / edited tasks. |
| Mean steps | Mean number of model decision rounds per task. |
| End-to-end time | Mean wall-clock duration from `run_started` to `run_finished`. |
| Failure categories | Counts of localization, patch, test, policy, model, and step-limit failures. |

## Evaluation protocol

1. Run each task in a fresh workspace and record `run.jsonl`.
2. Retain the exact model, prompt, command policy, step limit, and fixture revision.
3. A task is successful only when the latest code revision has a command with exit code zero
   that did not modify the workspace.
4. Inspect failures from the event log; do not infer success from the model's final text.
5. Report per-task results and aggregate metrics separately. Do not label the result as a
   SWE-bench score unless the official SWE-bench setup is actually run.

## Deterministic smoke test

```bash
python3 -m unittest discover -s tests -v
python3 examples/scripted_demo.py
```

The scripted trajectory must show a failing test, source inspection, one patch, a successful
rerun, and then completion. Its verification exit-code sequence is `[1, 0]`.
