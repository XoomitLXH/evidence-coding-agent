# Coding Agent Literature Basis

This project uses a deliberately small, local coding agent. The references below motivate
the design but do not prove that this implementation matches their reported results.

## What recent work does

| Work | Method contribution | Project adoption |
|---|---|---|
| RepoCoder (2023) | Iterates retrieval and generation to use repository-wide context. | Provide `search_code` and require inspection before editing. |
| Reflexion (2023) | Turns execution feedback into textual reflection stored across attempts. | Keep a bounded failure ledger that is fed back to the next turn. It is not persistent cross-task learning. |
| SWE-bench (2023) | Uses real issue descriptions, repositories, and test-based evaluation. | Treat command results as evidence; use a small deterministic repair fixture, not a SWE-bench score. |
| CodeAct (2024) | Uses executable code as a composable action space. | Retain composable actions, but expose them as narrow audited tools instead of an unrestricted interpreter. |
| SWE-agent (2024) | Shows that an agent-computer interface (ACI) affects navigation, editing, and execution. | Use a compact, explicit file/search/patch/command interface with structured results. |
| Agentless (2024) | Separates localization, repair, and patch validation into a simple workflow. | Enforce `EXPLORE -> EDIT -> VERIFY -> COMPLETE` rather than relying on a free-form loop. |

## What is standard and what is differentiated

The following are current standard practice, not claimed as original research: LLM tool
calling, code search, file editing, local command execution, test feedback, and a bounded
agent loop.

The project-level differentiation is a reliability-oriented combination:

1. **Revision-aware evidence gate.** Every write increments a revision and invalidates
   earlier evidence. `finish` is accepted only after a zero-exit, non-mutating verification
   command for the latest revision.
2. **Risk-aware execution policy.** Commands execute only inside the requested workspace;
   destructive, privilege-escalating, remote-push, piping, redirection, and shell-chaining
   forms are rejected.
3. **Replayable failure ledger.** JSONL records timestamps, tool arguments/results, state,
   failed commands, and verification evidence for debugging and demonstration.

These are engineering contributions for a course project, not a claim of a novel learning
algorithm, a new benchmark, or state-of-the-art performance.

## Primary sources

- Zhang et al. (2023). *RepoCoder: Repository-Level Code Completion Through Iterative Retrieval and Generation.* https://arxiv.org/abs/2303.12570
- Shinn et al. (2023). *Reflexion: Language Agents with Verbal Reinforcement Learning.* https://arxiv.org/abs/2303.11366
- Jimenez et al. (2023). *SWE-bench: Can Language Models Resolve Real-World GitHub Issues?* https://arxiv.org/abs/2310.06770
- Wang et al. (2024). *Executable Code Actions Elicit Better LLM Agents.* https://arxiv.org/abs/2402.01030
- Yang et al. (2024). *SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering.* https://arxiv.org/abs/2405.15793
- Xia et al. (2024). *Agentless: Demystifying LLM-based Software Engineering Agents.* https://arxiv.org/abs/2407.01489

Metadata and abstracts were checked against the arXiv API on 2026-08-27.
