# Codex-Only Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create an independent Codex-only usage monitor without any Claude access.

**Architecture:** Copy maintained source assets and tests into a new non-git project, then reduce the provider model and UI to Codex. Keep one startup-only Nitter alert source and local read state.

**Tech Stack:** Python 3.14, CustomTkinter, Win32/GDI+, pyte.

## Global Constraints

- Do not edit or delete `D:\Desktop\Codex-Claude-Usage-Monitor`.
- Do not read Claude credentials, invoke Claude CLI, or send Claude requests.
- Keep message-card outer dimensions unchanged.

---

### Task 1: Migrate the maintainable project files

**Files:**
- Copy source, tests, assets, build files, and project metadata into `D:\Desktop\Codex-Usage-Monitor`.
- Exclude `.git`, `.venv`, build outputs, caches, and Claude-only tests.

- [ ] Copy files and create a clean virtual environment.
- [ ] Install editable runtime and test dependencies.
- [ ] Run baseline tests.

### Task 2: Reduce data and UI to Codex

**Files:**
- Modify: `src/usage_overlay/main.py`
- Modify: `src/usage_overlay/models.py`
- Modify: `src/usage_overlay/native_ui.py`
- Modify: `src/usage_overlay/panel.py`
- Modify: `src/usage_overlay/reset_feed.py`
- Delete: `src/usage_overlay/providers/claude.py`
- Test: `tests/test_native_ui.py`

- [ ] Add a failing test for Codex-only compact rendering.
- [ ] Remove the Claude provider, toggles, source account, and source card icon.
- [ ] Make the taskbar click always open the panel.
- [ ] Run targeted tests.

### Task 3: Finish Codex styling and message behavior

**Files:**
- Modify: `src/usage_overlay/panel.py`
- Modify: `src/usage_overlay/formatting.py`
- Modify: `build.ps1`
- Modify: `tests/test_panel.py`

- [ ] Add failing tests for two-line preview and tooltip dismissal before browser open.
- [ ] Apply Codex pale-green colors and OpenAI application icon.
- [ ] Clamp card text and retain the timestamp bottom line.
- [ ] Run full tests and a startup smoke test.
