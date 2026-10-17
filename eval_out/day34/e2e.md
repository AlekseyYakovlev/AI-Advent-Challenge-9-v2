# Project files assistant: isolated-copy e2e

Date: 2026-10-17 14:19 UTC
Model: qwen/qwen3.5-9b

- PASS: write root is a scratch clone outside the working copy - <scratch>\files-root
- PASS: isolated copy patched to ports 18000/18001 - <scratch>\app
- PASS: isolated app instance is up (UI :18000 + Agent :18001)
- PASS: render: file header line is meta
- PASS: render: hunk line has the hunk class
- PASS: render: removed line has the removed class
- PASS: render: removed line starting with `-- ` is removed, not meta
- PASS: render: added line has the added class
- PASS: render: summary ends with the +A −R suffix - 🔧 project-files · edit_file — ok · +2 −2
- PASS: render: ok:false card has no diff region
- PASS: render: plain text result has no diff region
- PASS: render: <img onerror> inside a diff line creates no img element - img count 0
- PASS: project-files added through the «MCP серверы» form with --allow-write - <scratch>\app\mcp_servers\project_git.py
- PASS: goal in an ordinary chat: project-files write card is visible - 6 new card(s)
- PASS: write card contains the «Diff изменения» region
- PASS: clone contains the new or changed file - docs/notes/e2e-files-check.md
- PASS: read goal: a search_text / find_usages card appeared (not required)
- PASS: diff card survives a chat switch within the session
- PASS: isolated ports are free again
- PASS: user's working copy unchanged

RESULT: PASS (model qwen/qwen3.5-9b)
