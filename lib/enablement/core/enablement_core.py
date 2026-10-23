"""enablement_core -- deterministic helpers for rolling out AI agent tooling inside an organisation.

Stdlib only, no network, no model calls. Three parts:
  * skill lint   -- portable Agent Skills (SKILL.md) for a shared library: frontmatter, naming, references,
                    no machine-specific paths, no secrets.
  * adoption     -- weekly active usage, adoption rate and maturity per function, spend vs usage.
  * intake       -- rules-first screening of proposed AI tools / MCP connectors for a Tech + Legal forum.
The worker (../enablement-kit.py) wraps these as grokkit actions that print one line of JSON.
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

# --------------------------------------------------------------------------- skill lint
NAME_RX = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LINK_RX = re.compile(r"\]\((?!https?://|mailto:|#)([^)\s]+)\)")
ABS_RX = re.compile(r"(?:/home/[a-z_][\w.-]*/|/Users/[\w.-]+/|[A-Za-z]:\\\\?Users\\\\?[\w.-]+)")
SECRET_RX = re.compile(r"(?:\bsk-[A-Za-z0-9]{20,}|\bghp_[A-Za-z0-9]{30,}|\bAKIA[0-9A-Z]{16}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
TRIGGER_RX = re.compile(r"(?i)\b(use (it |this )?when|use for|when the user|when you need|trigger)")
PORTABLE_LOCATIONS = [".agents/skills/<name>/SKILL.md", ".github/skills/<name>/SKILL.md", "~/.agents/skills/<name>/SKILL.md"]
MAX_BODY_LINES = 500
MAX_DESCRIPTION = 1024


def parse_frontmatter(text: str) -> tuple[dict | None, str]:
    """Small YAML subset: `key: value`, quoted values, `key: >-` / `|` blocks, `[a, b]` lists. None if no frontmatter."""
    if not text.startswith("---"):
        return None, text
    lines = text.splitlines()
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None, text
    meta: dict = {}
    key = None
    for ln in lines[1:end]:
        if key and (ln.startswith("  ") or not ln.strip()):
            meta[key] = (meta[key] + " " + ln.strip()).strip()
            continue
        key = None
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", ln)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip()
        if v in (">", ">-", "|", "|-", ""):
            meta[k] = ""
            key = k
            continue
        if v.startswith("[") and v.endswith("]"):
            meta[k] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
        else:
            meta[k] = v.strip("'\"")
    return meta, "\n".join(lines[end + 1:])


def lint_skill(folder: str, text: str, files: set[str] | frozenset = frozenset()) -> list[dict]:
    """Issues for one SKILL.md. folder = its directory name; files = relative paths that exist next to it."""
    out: list[dict] = []
    add = lambda code, sev, msg: out.append({"code": code, "severity": sev, "msg": msg})  # noqa: E731
    meta, body = parse_frontmatter(text)
    if meta is None:
        add("no_frontmatter", "error", "SKILL.md must start with YAML frontmatter (--- name/description ---)")
        meta, body = {}, text
    name = str(meta.get("name", "")).strip()
    desc = str(meta.get("description", "")).strip()
    if not name:
        add("name_missing", "error", "frontmatter needs `name`")
    elif not NAME_RX.match(name) or len(name) > 64:
        add("name_format", "error", f"name {name!r}: lowercase letters, digits and single hyphens, max 64 chars")
    elif name != folder:
        add("name_dir_mismatch", "warn", f"name {name!r} differs from folder {folder!r}; some agents expect them equal")
    if not desc:
        add("description_missing", "error", "frontmatter needs `description` (what it does and when to use it)")
    else:
        if len(desc) > MAX_DESCRIPTION:
            add("description_too_long", "error", f"description is {len(desc)} chars (max {MAX_DESCRIPTION})")
        if not TRIGGER_RX.search(desc):
            add("description_no_trigger", "warn", "description should say when to use the skill, so agents load it at the right time")
    n = len(body.splitlines())
    if n > MAX_BODY_LINES:
        add("body_too_long", "warn", f"body has {n} lines; move detail into referenced files (max {MAX_BODY_LINES})")
    for ref in LINK_RX.findall(body):
        rel = ref.split("#", 1)[0].removeprefix("./")
        if rel and rel not in files:
            add("broken_ref", "error", f"referenced file not found next to SKILL.md: {ref}")
    if ABS_RX.search(text):
        add("absolute_path", "error", "machine-specific path (home directory / username) - not portable between users")
    if SECRET_RX.search(text):
        add("secret_like", "error", "looks like a credential - never ship keys in a shared skill")
    return out


def lint_tree(root: Path) -> dict:
    """Lint every <root>/<skill>/SKILL.md. Clean = no error-level issue."""
    root = Path(root)
    skills = []
    for sk in sorted(root.glob("*/SKILL.md")):
        d = sk.parent
        files = {str(p.relative_to(d)) for p in d.rglob("*") if p.is_file()}
        issues = lint_skill(d.name, sk.read_text(encoding="utf-8", errors="replace"), files)
        skills.append({"skill": d.name, "clean": not any(i["severity"] == "error" for i in issues), "issues": issues})
    return {"skills": len(skills), "clean": sum(s["clean"] for s in skills),
            "with_errors": [s["skill"] for s in skills if not s["clean"]], "results": skills,
            "portable_locations": PORTABLE_LOCATIONS}


# --------------------------------------------------------------------------- adoption
def load_events(path: Path) -> list[dict]:
    """CSV columns: week (e.g. 2026-W42), user, function, tool, sessions, cost_usd."""
    with open(path, newline="", encoding="utf-8") as f:
        return [{"week": r["week"], "user": r["user"], "function": r["function"], "tool": r.get("tool", ""),
                 "sessions": int(r["sessions"]), "cost_usd": float(r["cost_usd"])} for r in csv.DictReader(f)]


def weekly_active(events: list[dict], week: str, min_sessions: int = 1) -> dict[str, int]:
    """Distinct users per function with at least min_sessions sessions (summed over tools) in the week."""
    per: dict[tuple, int] = defaultdict(int)
    for e in events:
        if e["week"] == week:
            per[(e["function"], e["user"])] += e["sessions"]
    out: dict[str, int] = defaultdict(int)
    for (fn, _u), s in per.items():
        if s >= min_sessions:
            out[fn] += 1
    return dict(sorted(out.items()))


def adoption_rate(events: list[dict], headcount: dict[str, int], week: str) -> dict[str, float]:
    """Weekly active users / headcount per function in `headcount` (0.0 for no users or no headcount), 3 decimals."""
    wau = weekly_active(events, week)
    return {fn: (round(wau.get(fn, 0) / hc, 3) if hc else 0.0) for fn, hc in sorted(headcount.items())}


def power_share(events: list[dict], week: str, threshold: int = 10) -> dict[str, float]:
    """Share of a function's active users with >= threshold sessions in the week, 3 decimals."""
    per: dict[tuple, int] = defaultdict(int)
    for e in events:
        if e["week"] == week:
            per[(e["function"], e["user"])] += e["sessions"]
    act: dict[str, int] = defaultdict(int)
    pw: dict[str, int] = defaultdict(int)
    for (fn, _u), s in per.items():
        if s >= 1:
            act[fn] += 1
            pw[fn] += s >= threshold
    return {fn: round(pw[fn] / act[fn], 3) for fn in sorted(act)}


def maturity(rate: float, power: float) -> int:
    """0 none, 1 pilot (<25 %), 2 spreading (<50 %), 3 established (<75 % or few power users), 4 embedded."""
    if rate <= 0:
        return 0
    if rate < 0.25:
        return 1
    if rate < 0.5:
        return 2
    if rate < 0.75 or power < 0.2:
        return 3
    return 4


def spend_flags(events: list[dict], week: str, ratio: float = 1.5) -> list[str]:
    """Functions whose share of the week's cost exceeds `ratio` x their share of sessions (sorted)."""
    cost: dict[str, float] = defaultdict(float)
    ses: dict[str, int] = defaultdict(int)
    for e in events:
        if e["week"] == week:
            cost[e["function"]] += e["cost_usd"]
            ses[e["function"]] += e["sessions"]
    tc, ts = sum(cost.values()), sum(ses.values())
    if tc <= 0 or ts <= 0:
        return []
    return sorted(fn for fn in cost if cost[fn] / tc > ratio * (ses[fn] / ts))


def adoption_report(events: list[dict], headcount: dict[str, int], week: str, target: float = 0.5) -> dict:
    """Per-function KPI rows for a leadership update + functions below target + cost per active user."""
    rate = adoption_rate(events, headcount, week)
    pw = power_share(events, week)
    wau = weekly_active(events, week)
    cost: dict[str, float] = defaultdict(float)
    for e in events:
        if e["week"] == week:
            cost[e["function"]] += e["cost_usd"]
    rows = [{"function": fn, "headcount": headcount[fn], "weekly_active": wau.get(fn, 0), "adoption_rate": rate[fn],
             "power_share": pw.get(fn, 0.0), "maturity": maturity(rate[fn], pw.get(fn, 0.0)),
             "cost_usd": round(cost.get(fn, 0.0), 2),
             "cost_per_active_usd": round(cost.get(fn, 0.0) / wau[fn], 2) if wau.get(fn) else None}
            for fn in sorted(headcount)]
    total_active = sum(wau.get(fn, 0) for fn in headcount)
    total_hc = sum(headcount.values())
    return {"week": week, "target": target, "rows": rows,
            "overall_rate": round(total_active / total_hc, 3) if total_hc else 0.0,
            "below_target": [r["function"] for r in rows if r["adoption_rate"] < target],
            "spend_flags": spend_flags(events, week)}


# --------------------------------------------------------------------------- governance intake
EU_EEA = {"eu", "eea", "se", "eu/eea"}
DATA_CLASSES = ("public", "internal", "confidential", "restricted")


def screen(p: dict) -> dict:
    """Rules-first screening of one proposal for the Tech + Legal AI governance forum.
    decision: fast-track (no review needed) | review (forum agenda) | reject (blocked by a hard rule)."""
    reasons: list[str] = []
    reviews: set[str] = set()
    dc = str(p.get("data_class", "")).lower()
    region = str(p.get("vendor_region", "")).lower()
    if dc not in DATA_CLASSES:
        reasons.append("data classification missing or unknown - classify before review")
        reviews.add("tech")
    if dc == "restricted" and region not in EU_EEA:
        return {"id": p.get("id"), "decision": "reject", "reviews": [], "priority": 0,
                "reasons": ["restricted data with a vendor outside the EU/EEA"]}
    if dc in ("confidential", "restricted"):
        reviews.update({"tech", "legal"})
        reasons.append(f"{dc} data")
    if p.get("personal_data"):
        reviews.add("legal")
        reasons.append("processes personal data (GDPR: legal basis, DPA, retention)")
    if any(str(s).lower() in ("write", "admin", "delete") for s in p.get("scopes", [])):
        reviews.add("tech")
        reasons.append("connector can write or administer systems")
    if p.get("type") == "mcp_connector" and not p.get("allowlisted_tools"):
        reviews.add("tech")
        reasons.append("MCP connector without a tool allowlist")
    if p.get("external_output") and not p.get("human_review"):
        reviews.add("tech")
        reasons.append("output leaves the company without a mandatory human verification step")
    if region and region not in EU_EEA and dc not in ("public",):
        reviews.add("legal")
        reasons.append("vendor outside the EU/EEA (transfer mechanism needed)")
    decision = "review" if reviews else "fast-track"
    prio = (2 if "legal" in reviews else 0) + (1 if "tech" in reviews else 0) + len(reasons)
    return {"id": p.get("id"), "decision": decision, "reviews": sorted(reviews), "priority": prio,
            "reasons": reasons or ["public/internal data, read-only, human verification in place"]}


def agenda(proposals: list[dict]) -> dict:
    """Screen all proposals; agenda = review items, highest priority first, then id."""
    res = [screen(p) for p in proposals]
    review = sorted((r for r in res if r["decision"] == "review"), key=lambda r: (-r["priority"], str(r["id"])))
    return {"proposals": len(res), "fast_track": [r["id"] for r in res if r["decision"] == "fast-track"],
            "rejected": [r["id"] for r in res if r["decision"] == "reject"], "agenda": review, "all": res}
