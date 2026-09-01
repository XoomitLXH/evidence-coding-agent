# Local Plugin System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Claude Code/Codex-style local plugin discovery, skill injection, controlled plugin tools, and a first-party PDF reader.

**Architecture:** A `PluginManager` discovers and validates local manifests, indexes skills, and imports explicitly declared Python tool adapters. `ToolRegistry` merges core and plugin schemas while preserving existing policy/draft gates. `AgentLoop` builds a task-specific system prompt and passes instance tool schemas to both synchronous and streaming model clients.

**Tech Stack:** Python 3.10+, stdlib `json/pathlib/importlib`, optional `pypdf`/`pdfplumber`, existing unittest suite and CLI.

---

### Task 1: Plugin Manager and Manifest Validation

**Files:**
- Create: `coding_agent/plugins.py`
- Test: `tests/test_plugins.py`

- [ ] **Step 1: Write failing tests** for manifest discovery, malformed manifest warnings, path precedence, skill indexing, and explicit/automatic selection.
- [ ] **Step 2: Run `python3 -m unittest tests.test_plugins -v` and confirm failures because `PluginManager` is absent.
- [ ] **Step 3: Implement `PluginManager`, `Plugin`, `Skill` dataclasses, safe manifest parsing, discovery from explicit/project/user/builtin directories, bounded `SKILL.md` loading, and `select_skills(task, explicit)`.
- [ ] **Step 4: Run the focused tests and confirm all pass.

### Task 2: Controlled Tool Adapters and PDF Reader

**Files:**
- Create: `coding_agent/plugin_tools.py`
- Create: `coding_agent/pdf_tools.py`
- Test: `tests/test_plugin_tools.py`
- Test: `tests/test_pdf_tools.py`

- [ ] **Step 1: Write failing tests** for tool schema validation/conflicts and PDF workspace/extension/size/page/dependency checks.
- [ ] **Step 2: Run focused tests and confirm failures.
- [ ] **Step 3: Implement adapter loading from a manifest `tools` path, a registry callback contract, and `read_pdf` using optional `pypdf` then `pdfplumber` with bounded output.
- [ ] **Step 4: Add a built-in PDF plugin manifest/adapter under `coding_agent/builtin_plugins/pdf/` and a minimal Superpowers manifest alias pointing at the local cache when available.
- [ ] **Step 5: Run focused tests and confirm all pass.

### Task 3: Dynamic Agent Context and Registry Integration

**Files:**
- Modify: `coding_agent/tool_registry.py`
- Modify: `coding_agent/agent_loop.py`
- Modify: `coding_agent/web.py`
- Test: `tests/test_agent_loop.py`
- Test: `tests/test_tool_registry.py`

- [ ] **Step 1: Add tests asserting selected skill text is in the system message and dynamically registered schemas are passed to `complete` and `stream`.
- [ ] **Step 2: Run the focused tests and confirm failures.
- [ ] **Step 3: Add `plugin_manager`/`plugin_dirs`/`explicit_skills` parameters, expose `registry.tool_specs`, use it for all model calls, and include plugin metadata in reports without changing existing gates.
- [ ] **Step 4: Update web task creation to accept optional plugin settings and expose loaded plugin metadata.
- [ ] **Step 5: Run all agent/registry tests.

### Task 4: CLI and Documentation

**Files:**
- Modify: `coding_agent/cli.py`
- Modify: `README.txt`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests for `--plugin-dir`, `--list-plugins`, and `--skills` parsing.
- [ ] **Step 2: Implement CLI flags and human-readable plugin listing while preserving JSON task output.
- [ ] **Step 3: Document directory layout, manifest example, skill selection, PDF limits, and optional dependencies.
- [ ] **Step 4: Run the complete suite with `python3 -m unittest discover -s tests -v`.

### Task 5: Verification

**Files:**
- Modify: none

- [ ] **Step 1: Run `python3 -m unittest discover -s tests -v` and record the result.
- [ ] **Step 2: Run a temporary workspace smoke test that loads a fixture plugin, selects a skill, and lists the PDF tool schema.
- [ ] **Step 3: Inspect `git diff --check` and confirm no unrelated files were changed.
