# Phase 18: LAN exposure (Day 30) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-10-12
**Phase:** 18-LAN exposure (Day 30)
**Areas discussed:** Origin check rule, run.py output in LAN mode, Invalid BIND_HOST, Docs and Firewall

---

## Origin check rule

### How to decide an Origin is "ours"

| Option | Description | Selected |
|--------|-------------|----------|
| Origin host = request Host | Accept when the Origin host equals the Host header host and the port is UI_PORT; works for any IP and machine name | ✓ |
| Origin from server addresses | Collect interface IPv4 addresses and hostname at startup; breaks on DHCP change until restart | |
| Any host on UI_PORT | Accept any `http://<anything>:8000`; a site served on port 8000 elsewhere would pass | |

**User's choice:** Origin host = request Host (recommended)

### When the rule applies

| Option | Description | Selected |
|--------|-------------|----------|
| LAN mode only | Loopback keeps the static list, existing behaviour untouched | |
| Always, both modes | One rule, no mode branch | ✓ |

**User's choice:** Always, both modes (recommended option was "LAN mode only")

### WebSocket leniencies (missing Origin, `Origin: null`)

| Option | Description | Selected |
|--------|-------------|----------|
| No Origin yes, null no | Missing Origin stays allowed (non-browser clients); `null` rejected in both modes | ✓ |
| Keep both | No change; `null` stays a loophole | |
| Reject null only in LAN | Two behaviours | |

**User's choice:** No Origin yes, null no (recommended)

### Static origin lists

| Option | Description | Selected |
|--------|-------------|----------|
| Keep loopback :UI_PORT | localhost/127.0.0.1 on UI_PORT always allowed plus the rule; drop `:8001` WS variants; one shared place | ✓ |
| Rule only, remove lists | Cleaner, but many tests need rewriting | |
| Keep everything incl. :8001 | Only add the rule; duplicated lists remain | |

**User's choice:** Keep loopback :UI_PORT (recommended)

---

## run.py output in LAN mode

### Which addresses to print

| Option | Description | Selected |
|--------|-------------|----------|
| All IPv4 + machine name | One line per non-loopback IPv4, plus hostname URL and 127.0.0.1 | ✓ |
| Primary IP only | Default-route address; may pick the wrong adapter with a VPN | |
| All IPv4, no name | IPs only | |

**User's choice:** All IPv4 + machine name (recommended)

### Exposure warning

| Option | Description | Selected |
|--------|-------------|----------|
| Framed block, English | Same style as the bootstrap-admin banner | ✓ |
| Framed block, Russian | Risk of mojibake in a non-UTF-8 Windows console | |
| Single WARNING line | Less visible | |

**User's choice:** Framed block, English (recommended)

### LAN mode shown in the browser UI

| Option | Description | Selected |
|--------|-------------|----------|
| No, console only | NET-05 only asks for run.py output | |
| "Сеть" badge in the header | Needs a new API field and app.js changes | |
| Line in Settings | Mode and addresses in the Settings modal; endpoint + UI | ✓ |

**User's choice:** Line in Settings (recommended option was "console only")

### What Settings shows

| Option | Description | Selected |
|--------|-------------|----------|
| Mode + addresses, always | Read-only "Сеть" section in both modes; click copies the link; authenticated endpoint | ✓ |
| LAN mode only | Section appears only with 0.0.0.0 | |
| Mode only, no addresses | Server does not disclose interfaces | |

**User's choice:** Mode + addresses, always (recommended)

---

## Invalid BIND_HOST

### Strictness

| Option | Description | Selected |
|--------|-------------|----------|
| Exactly two values | `127.0.0.1` and `0.0.0.0`; unset/empty = loopback | |
| Also accept `localhost` | `localhost` is a synonym for 127.0.0.1 | ✓ |
| Empty string is an error | Unset = loopback, `BIND_HOST=` refuses | |

**User's choice:** Also accept `localhost` (recommended option was "exactly two values")
**Notes:** Amends the wording of NET-01/NET-02.

### Where validation fires

| Option | Description | Selected |
|--------|-------------|----------|
| Shared config, everywhere | `shared/config.py`; run.py, standalone uvicorn and tests all refuse | ✓ |
| run.py only | Standalone uvicorn bypasses it | |

**User's choice:** Shared config, everywhere (recommended)

### What the operator sees

| Option | Description | Selected |
|--------|-------------|----------|
| Short explanation, exit code 2 | No traceback, English, stderr, before port cleanup and admin bootstrap | ✓ |
| Same, exit code 1 | | |
| Claude's discretion | | |

**User's choice:** Short explanation, exit code 2 (recommended)

---

## Docs and Firewall

### Where the instructions go

| Option | Description | Selected |
|--------|-------------|----------|
| Section in docs/USER_GUIDE.md | Plus `.env.example` comment and an Origin note in docs/ARCHITECTURE.md | ✓ |
| Separate docs/LAN_MODE.md | | |
| README.md | | |

**User's choice:** Section in docs/USER_GUIDE.md (recommended)

### Firewall instruction format

| Option | Description | Selected |
|--------|-------------|----------|
| PowerShell command + GUI steps | `New-NetFirewallRule`, GUI steps, removal command | ✓ |
| PowerShell command only | | |
| Script in scripts/ | New .ps1 to maintain | |

**User's choice:** PowerShell command + GUI steps (recommended)

### Rule scope

| Option | Description | Selected |
|--------|-------------|----------|
| Private profile only | `-Profile Private`, with a note on changing the network category | ✓ |
| Private + local subnet | Adds `-RemoteAddress LocalSubnet` | |
| All profiles | Also opens ports on public networks | |

**User's choice:** Private profile only (recommended)

---

## Claude's Discretion

- How the host-match rule is wired into REST CORS (middleware vs regex)
- Whether the UI server's CORS reuses the shared check
- Path and shape of the network-info endpoint; placement of the "Сеть" section
- Exact wording of the banner, error message and UI strings
- Case handling for `localhost`; behaviour with no usable Host header
- How the supervisor passes the host to the Agent subprocess

## Deferred Ideas

None.
