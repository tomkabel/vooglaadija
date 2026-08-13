"""Weekly repo audit report generator for Vooglaadija.

Executable architecture governance (see docs/ARCHITECTURE-STANDARD.md):
- ``--weekly``  : full deep scan -> audit-report.md + audit-report.json (advisory,
                  ranked by hotspot score = complexity violations x git churn).
- ``--fix``     : mechanical auto-fix (safe ruff fixes + formatting); prints a
                  changed-file manifest for the [AUTO-BOT] cleanup PR.
- ``--baseline``: (re)generate .github/audit-baseline.json from current measurements.

Exit code is 0 unless the script itself fails (advisory model); gate findings
(F401/F841, TID251, boundary, vulture, deptry, lockfile, secrets) are reported
separately from measure findings (complexity, hotspots, temporal coupling,
defensive density, duplication, commented-out code).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("app", "core", "worker", "scripts", "alembic")
JS_DIRS = ("app/static", "frontend")
CHURN_DAYS = 90
MIN_CO_COMMITS = 5
CO_CHANGE_RATIO = 0.8
HOTSPOT_LIMIT = 10
COMPLEXITY_CONFIG = ROOT / "scripts" / "audit" / "ruff-complexity.toml"
VULTURE_WHITELIST = ROOT / "scripts" / "audit" / "vulture_whitelist.py"
BASELINE_PATH = ROOT / ".github" / "audit-baseline.json"

COMMENTED_CODE_RE = re.compile(
    r"^\s*#\s*(import |from |def |class |if |for |while |return |try:|except |"
    r"with |async |@)\b"
)
WINDOW_GLOBAL_RE = re.compile(r"\bwindow\.([A-Za-z_]\w*)\s*=")
TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")
VULTURE_LINE_RE = re.compile(
    r"^(?P<path>[^:]+):(?P<line>\d+):\s+(?P<name>.+?)\s+\((?P<conf>\d+)% confidence\)$"
)
SECRET_LINE_RE = re.compile(
    r"^(?P<path>[^:]+):(?P<line>\d+):.*Secret detected: (?P<secret_type>.+)$"
)


def run(
    cmd: list[str], timeout: int = 120, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with a timeout, capturing output as text."""
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or ROOT,
            check=False,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", f"not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", f"timed out after {timeout}s")


def python_files() -> list[Path]:
    """All tracked .py files under the source dirs."""
    files: list[Path] = []
    for source_dir in SOURCE_DIRS:
        root = ROOT / source_dir
        if root.exists():
            files.extend(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(files)


def git_churn() -> dict[str, int]:
    """Per-file commit counts over the churn window (relative paths)."""
    proc = run(["git", "log", "--name-only", "--pretty=format:", f"--since={CHURN_DAYS} days ago"])
    counts: Counter[str] = Counter()
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if line and not line.startswith((".git", "docs/", "research/")):
            counts[line] += 1
    return dict(counts)


def temporal_coupling() -> list[dict[str, object]]:
    """File pairs co-changed in the same commits (>= 80% of the smaller file's commits)."""
    commit_files = _iter_commit_file_sets()

    per_file: Counter[str] = Counter()
    for files in commit_files:
        per_file.update(files)
    pairs: Counter[tuple[str, str]] = Counter()
    for files in commit_files:
        sorted_files = sorted(files)
        for i, first in enumerate(sorted_files):
            for second in sorted_files[i + 1 :]:
                pairs[(first, second)] += 1

    findings: list[dict[str, object]] = []
    for (first, second), co_count in pairs.items():
        if co_count < MIN_CO_COMMITS:
            continue
        ratio = co_count / min(per_file[first], per_file[second])
        if ratio >= CO_CHANGE_RATIO:
            findings.append(
                {
                    "files": [first, second],
                    "co_commits": co_count,
                    "ratio": round(ratio, 2),
                    "commits": {first: per_file[first], second: per_file[second]},
                }
            )
    return sorted(findings, key=lambda item: item["co_commits"], reverse=True)


def _iter_commit_file_sets() -> list[set[str]]:
    """Yield the set of tracked source files touched by each commit."""
    proc = run(["git", "log", "--name-only", "--pretty=format:", f"--since={CHURN_DAYS} days ago"])
    commit_files: list[set[str]] = []
    current: set[str] = set()
    for raw_line in proc.stdout.splitlines():
        if not raw_line.strip():
            if current:
                commit_files.append(current)
                current = set()
        else:
            line = raw_line.strip()
            if not line.startswith((".git", "docs/", "research/")):
                current.add(line)
    if current:
        commit_files.append(current)
    return commit_files


def complexity_violations() -> list[dict[str, object]]:
    """Violations of the strict complexity thresholds (measure, not gate)."""
    proc = run(
        [
            "ruff",
            "check",
            "--config",
            str(COMPLEXITY_CONFIG),
            "--output-format=json",
            *SOURCE_DIRS,
        ],
        timeout=180,
    )
    violations: list[dict[str, object]] = []
    if not proc.stdout.strip().startswith("["):
        return violations
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return violations
    for entry in raw:
        violations.append(
            {
                "path": _relative(entry["filename"]),
                "line": entry.get("location", {}).get("row"),
                "code": entry["code"],
                "message": entry["message"],
            }
        )
    return violations


def _relative(path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return path


def defensive_density() -> list[dict[str, object]]:
    """try/except count per 100 LOC per module (Ousterhout: define errors out of existence)."""
    rows: list[dict[str, object]] = []
    for path in python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            lines = len(path.read_text(encoding="utf-8").splitlines())
        except (SyntaxError, OSError):
            continue
        try_count = sum(1 for node in ast.walk(tree) if isinstance(node, ast.Try))
        if try_count:
            density = round(try_count * 100 / max(lines, 1), 1)
            rows.append(
                {
                    "path": _relative(str(path)),
                    "try_except": try_count,
                    "lines": lines,
                    "per_100": density,
                }
            )
    return sorted(rows, key=lambda row: row["per_100"], reverse=True)[:10]


def commented_out_code() -> list[str]:
    """Python files containing commented-out code lines."""
    findings: list[str] = []
    for path in python_files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        if any(COMMENTED_CODE_RE.match(line) for line in lines):
            findings.append(_relative(str(path)))
    return sorted(findings)


def window_globals() -> list[dict[str, object]]:
    """JS assignments to window.* (frontend policy: ES modules, no new globals)."""
    findings: list[dict[str, object]] = []
    for js_dir in JS_DIRS:
        root = ROOT / js_dir
        if not root.exists():
            continue
        for path in root.rglob("*.js"):
            if "node_modules" in path.parts or "dist" in path.parts:
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if match := WINDOW_GLOBAL_RE.search(line):
                    findings.append(
                        {"path": _relative(str(path)), "line": lineno, "global": match.group(1)}
                    )
    return findings


def vulture_findings() -> list[dict[str, object]]:
    """Dead code via vulture (>=80% confidence).

    Whitelist (scripts/audit/vulture_whitelist.py) is auto-detected by vulture
    via its "# vulture whitelist" marker because scripts/ is part of the scan.
    """
    cmd = [
        "vulture",
        *SOURCE_DIRS,
        "--min-confidence",
        "80",
    ]
    proc = run(cmd, timeout=180)
    findings: list[dict[str, object]] = []
    for line in proc.stdout.splitlines():
        if match := VULTURE_LINE_RE.match(line):
            findings.append(
                {
                    "path": match.group("path"),
                    "line": int(match.group("line")),
                    "name": match.group("name"),
                    "confidence": int(match.group("conf")),
                }
            )
    return findings


def deptry_findings() -> tuple[list[dict[str, object]], str]:
    """Unused/missing/misplaced dependencies via deptry."""
    proc = run(["deptry", ".", "--output-format", "json"], timeout=180)
    if proc.returncode not in (0, 1) or not proc.stdout.strip().startswith("{"):
        proc = run(["deptry", "."], timeout=180)
        return [], proc.stdout.strip()
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return [], proc.stdout.strip()
    findings = []
    for issue in raw.get("issues", []):
        findings.append(
            {
                "type": issue.get("type"),
                "module": issue.get("module"),
                "location": issue.get("location"),
                "message": issue.get("message"),
            }
        )
    return findings, ""


def jscpd_clones() -> tuple[list[dict[str, object]], str]:
    """Duplicated code blocks via jscpd (Rust-native, v5)."""
    executable = shutil.which("jscpd") or shutil.which("npx")
    if executable is None:
        return [], "jscpd not installed (npm i -g jscpd)"
    out_dir = Path(tempfile.mkdtemp(prefix="jscpd-", dir=str(ROOT)))
    cmd = (
        ["jscpd", "--min-tokens", "50", "--reporters", "json", "--output", str(out_dir)]
        if Path(executable).name == "jscpd"
        else [
            "npx",
            "--yes",
            "jscpd@latest",
            "--min-tokens",
            "50",
            "--reporters",
            "json",
            "--output",
            str(out_dir),
        ]
    )
    cmd += [
        "--format",
        "python,javascript,markup,css",
        "--ignore",
        "**/node_modules/**,**/.git/**,**/docs/**,**/research/**,**/infra/**,**/tests/**,"
        "**/uv.lock, **/package-lock.json, **/pnpm-lock.yaml",
        ".",
    ]
    proc = run(cmd, timeout=240)
    clones: list[dict[str, object]] = []
    report = out_dir / "jscpd-report.json"
    if report.exists():
        try:
            raw = json.loads(report.read_text(encoding="utf-8"))
            for dup in raw.get("duplicates", []):
                clones.append(
                    {
                        "files": sorted(
                            {
                                dup.get("firstFile", {}).get("name", ""),
                                dup.get("secondFile", {}).get("name", ""),
                            }
                        ),
                        "fragment": (dup.get("fragment") or "")[:160],
                    }
                )
        except (json.JSONDecodeError, OSError):
            pass
    shutil.rmtree(out_dir, ignore_errors=True)
    if not clones and proc.returncode not in (0, 1):
        return [], proc.stderr.strip()[:300] or "jscpd failed"
    return clones, ""


def secrets_delta() -> list[dict[str, object]]:
    """Secrets found against the committed baseline (detect-secrets hook)."""
    hook = shutil.which("detect-secrets-hook")
    if hook is None:
        return []
    baseline = ROOT / ".secrets.baseline"
    if not baseline.exists():
        return []
    files = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    ).stdout.splitlines()
    excluded = (
        "app/static/js/htmx.min.js",
        "frontend/css/dist/",
        "frontend/pnpm-lock.yaml",
        "infra/ssl/",
        "pnpm-lock.yaml",
        "uv.lock",
    )
    files = [f for f in files if not any(f.startswith(ex) for ex in excluded)]
    proc = run([hook, "--baseline", str(baseline), *files], timeout=240)
    findings: list[dict[str, object]] = []
    for line in proc.stdout.splitlines():
        if match := SECRET_LINE_RE.match(line):
            findings.append(
                {
                    "path": match.group("path"),
                    "line": int(match.group("line")),
                    "type": match.group("secret_type"),
                }
            )
    return findings


def coverage_rates() -> dict[str, float]:
    """Per-file line coverage from coverage.xml (generated by the test suite)."""
    coverage_xml = ROOT / "coverage.xml"
    if not coverage_xml.exists():
        return {}
    try:
        root = ET.parse(coverage_xml).getroot()
    except ET.ParseError:
        return {}
    rates: dict[str, float] = {}
    for cls in root.iter("class"):
        name = cls.get("filename", "")
        line_rate = cls.get("line-rate")
        if name and line_rate is not None:
            try:
                rates[name] = float(line_rate)
            except ValueError:
                continue
    return rates


def lockfile_check() -> tuple[bool, str]:
    """One-Version Rule: lockfile must be up to date with pyproject.toml."""
    proc = run(["uv", "lock", "--check"], timeout=120)
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()


def boundary_violations() -> list[dict[str, object]]:
    """Import-boundary violations (zone-aware AST verifier)."""
    proc = run(["python", "scripts/import_analysis.py"], timeout=120)
    violations: list[dict[str, object]] = []
    for line in proc.stdout.splitlines():
        if ":" in line and not line.startswith("Import boundary check"):
            path, rest = line.split(":", 1)
            violations.append({"path": path, "detail": rest.strip()})
    return violations


def unused_imports() -> list[dict[str, object]]:
    """Unused imports/variables (ruff F401/F841) — gate category."""
    proc = run(
        ["ruff", "check", "--select", "F401,F841", "--output-format=json", *SOURCE_DIRS],
        timeout=120,
    )
    findings: list[dict[str, object]] = []
    if not proc.stdout.strip().startswith("["):
        return findings
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return findings
    for entry in raw:
        findings.append(
            {
                "path": _relative(entry["filename"]),
                "line": entry.get("location", {}).get("row"),
                "code": entry["code"],
                "message": entry["message"],
            }
        )
    return findings


def tid251_violations() -> list[dict[str, object]]:
    """Banned imports (layering + yt-dlp ACL) from the main ruff config."""
    proc = run(
        ["ruff", "check", "--select", "TID251", "--output-format=json", *SOURCE_DIRS], timeout=120
    )
    findings: list[dict[str, object]] = []
    if not proc.stdout.strip().startswith("["):
        return findings
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return findings
    for entry in raw:
        findings.append(
            {
                "path": _relative(entry["filename"]),
                "line": entry.get("location", {}).get("row"),
                "code": entry["code"],
                "message": entry["message"],
            }
        )
    return findings


def large_files() -> list[dict[str, object]]:
    """Top-10 largest modules (informational only — deep modules are allowed)."""
    rows: list[dict[str, object]] = []
    for path in python_files():
        try:
            rows.append(
                {
                    "path": _relative(str(path)),
                    "lines": len(path.read_text(encoding="utf-8").splitlines()),
                }
            )
        except OSError:
            continue
    return sorted(rows, key=lambda row: row["lines"], reverse=True)[:10]


def todo_count() -> int:
    """Count of TODO/FIXME/HACK/XXX markers in source files."""
    total = 0
    for source_dir in SOURCE_DIRS:
        root = ROOT / source_dir
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                total += len(TODO_RE.findall(path.read_text(encoding="utf-8")))
            except OSError:
                continue
    return total


def hotspot_scores(
    complexity: list[dict[str, object]], churn: dict[str, int], coverage: dict[str, float]
) -> list[dict[str, object]]:
    """Hotspot model (Tornhill): complexity violations x churn, coverage-correlated."""
    per_file: Counter[str] = Counter()
    for entry in complexity:
        per_file[entry["path"]] += 1
    rows: list[dict[str, object]] = []
    for path, violations in per_file.items():
        commits = churn.get(path, 0)
        score = violations * commits
        rows.append(
            {
                "path": path,
                "complexity_violations": violations,
                "churn_commits": commits,
                "score": score,
                "coverage": coverage.get(path),
            }
        )
    ranked = sorted(rows, key=lambda row: row["score"], reverse=True)
    return ranked[:HOTSPOT_LIMIT]


def baseline_values(measures: dict[str, object]) -> dict[str, int]:
    """Flatten current measurements into baseline-comparable integers."""
    return {
        "boundary_violations": len(measures["boundary"]),
        "unused_imports": len(measures["unused_imports"]),
        "tid251_violations": len(measures["tid251"]),
        "dead_code": len(measures["dead_code"]),
        "unused_deps": len(measures["unused_deps"]),
        "secrets": len(measures["secrets"]),
        "complexity_violations": len(measures["complexity"]),
        "temporal_coupling_pairs": len(measures["temporal_coupling"]),
        "jscpd_clones": len(measures["jscpd"]),
        "commented_out_files": len(measures["commented_out"]),
        "window_globals": len(measures["window_globals"]),
        "todos": measures["todos"],
        "lock_check_ok": 1 if measures["lock_ok"] else 0,
    }


def trend_table(current: dict[str, int], baseline: dict[str, int]) -> list[dict[str, object]]:
    """Deltas vs the committed baseline (updated only via maintainer PR)."""
    rows: list[dict[str, object]] = []
    for key in sorted(current):
        previous = baseline.get(key, 0)
        delta = current[key] - previous
        state = "improved" if delta < 0 else ("regressed" if delta > 0 else "stable")
        rows.append(
            {
                "metric": key,
                "baseline": previous,
                "current": current[key],
                "delta": delta,
                "state": state,
            }
        )
    return rows


def fix_commands(measures: dict[str, object]) -> list[str]:
    """Exact remediation commands per finding category (shift-left, SWE@Google)."""
    commands: list[str] = []
    if measures["unused_imports"]:
        commands.append("ruff check --fix app/ core/ worker/ scripts/ alembic/ tests/")
    if measures["unused_deps"]:
        commands.append("deptry .   # then: uv remove <dependency> for each unused dep")
    if measures["dead_code"]:
        commands.append(
            "vulture app core worker scripts alembic --min-confidence 80  # delete or whitelist items"
        )
    if not measures["lock_ok"]:
        commands.append("uv lock && uv sync")
    if measures["secrets"]:
        commands.append(
            "rotate the leaked secret, then: detect-secrets scan | detect-secrets audit .secrets.baseline"
        )
    if measures["complexity"]:
        commands.append(
            "ruff check --config scripts/audit/ruff-complexity.toml app/ core/ worker/ scripts/ alembic/  "
            "# refactor hotspots behind stable facades (Strangler Fig), never arbitrary splitting"
        )
    return commands


def render_markdown(measures: dict[str, object], trend: list[dict[str, object]]) -> str:
    """Render the human-readable report (ranked by hotspot score)."""
    out: list[str] = [
        f"# Weekly Repo Audit — {datetime.now(UTC).date().isoformat()}",
        "",
        f"Gate findings: **{len(measures['gate_summary'])}** | Measure findings: **{len(measures['measure_summary'])}** | "
        f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]
    out.append("## Gates (would fail PR CI)")
    gate_sections = [
        ("Boundary violations", "boundary"),
        ("Unused imports/variables (F401/F841)", "unused_imports"),
        ("Banned imports (TID251: layering + yt-dlp ACL)", "tid251"),
        ("Dead code (vulture >=80%)", "dead_code"),
        ("Unused dependencies (deptry)", "unused_deps"),
        ("Lockfile (uv lock --check)", "lock_ok"),
        ("Secrets delta", "secrets"),
    ]
    for title, key in gate_sections:
        if key == "lock_ok":
            if measures["lock_ok"]:
                out.append("### Lockfile (uv lock --check): OK")
            else:
                out.append(f"### Lockfile (uv lock --check): STALE — {measures['lock_note']}")
            continue
        out.append(f"### {title}: {len(measures[key])}")
        for item in measures[key][:10]:
            if isinstance(item, dict):
                out.append(
                    f"- `{item.get('path', '')}:{item.get('line', '')}` {item.get('message', item.get('name', ''))}"
                )
    out.append("")
    out.append("## Measures (advisory, ranked by hotspot score)")
    out.append("### Hotspot ranking (complexity violations x churn)")
    if measures["hotspots"]:
        for row in measures["hotspots"]:
            cov = f", coverage {row['coverage']:.0%}" if row.get("coverage") is not None else ""
            out.append(
                f"- `{row['path']}` score={row['score']} ({row['complexity_violations']} violations x "
                f"{row['churn_commits']} commits{cov})"
            )
    else:
        out.append("- No hotspots above the rank threshold.")
    out.append("")
    out.append("### Temporal coupling (co-changed >=80%)")
    if measures["temporal_coupling"]:
        for pair in measures["temporal_coupling"][:10]:
            out.append(
                f"- {pair['co_commits']}x {pair['files'][0]} <-> {pair['files'][1]} (ratio {pair['ratio']})"
            )
    else:
        out.append("- No temporal coupling pairs above the threshold.")
    out.append("")
    out.append("### Complexity (strict pass)")
    out.append(f"- {len(measures['complexity'])} violations")
    for entry in measures["complexity"][:10]:
        out.append(f"- `{entry['path']}:{entry['line']}` {entry['code']} {entry['message']}")
    out.append("")
    out.append("### Defensive code density (try/except per 100 LOC)")
    for row in measures["defensive"][:5]:
        out.append(
            f"- `{row['path']}` {row['per_100']}/100 LOC ({row['try_except']} try/except in {row['lines']} lines)"
        )
    out.append("")
    out.append("### Duplication (jscpd)")
    if measures["jscpd"]:
        out.append(f"- {len(measures['jscpd'])} clone pairs")
        for clone in measures["jscpd"][:5]:
            out.append(f"- {clone['files']}")
    elif measures["jscpd_note"]:
        out.append(f"- {measures['jscpd_note']}")
    else:
        out.append("- No clones above min-tokens=50.")
    out.append("")
    out.append("### Other measures")
    out.append(f"- Commented-out code: {len(measures['commented_out'])} files")
    out.append(f"- window.* globals: {len(measures['window_globals'])}")
    out.append(f"- TODO/FIXME markers: {measures['todos']}")
    largest = ", ".join(f"{row['path']} ({row['lines']})" for row in measures["large_files"][:5])
    out.append(f"- Largest modules: {largest}")
    out.append("")
    out.append("## Trend vs baseline")
    out.append("| metric | baseline | current | delta | state |")
    out.append("|---|---|---|---|---|")
    for row in trend:
        out.append(
            f"| {row['metric']} | {row['baseline']} | {row['current']} | {row['delta']:+d} | {row['state']} |"
        )
    out.append("")
    out.append("## Fix commands")
    for cmd in measures["fix_commands"] or ["- All clear."]:
        out.append(f"```\n{cmd}\n```")
    return "\n".join(out)


def collect_measures() -> dict[str, object]:
    """Run every check and assemble the measurement payload."""
    churn = git_churn()
    complexity = complexity_violations()
    coverage = coverage_rates()
    unused = unused_imports()
    tid251 = tid251_violations()
    boundary = boundary_violations()
    dead = vulture_findings()
    deps, deptry_note = deptry_findings()
    clones, jscpd_note = jscpd_clones()
    secrets = secrets_delta()
    lock_ok, lock_note = lockfile_check()
    measures: dict[str, object] = {
        "generated": datetime.now(UTC).isoformat(),
        "boundary": boundary,
        "unused_imports": unused,
        "tid251": tid251,
        "dead_code": dead,
        "unused_deps": deps,
        "deptry_note": deptry_note,
        "lock_ok": lock_ok,
        "lock_note": lock_note,
        "secrets": secrets,
        "complexity": complexity,
        "hotspots": hotspot_scores(complexity, churn, coverage),
        "temporal_coupling": temporal_coupling(),
        "defensive": defensive_density(),
        "jscpd": clones,
        "jscpd_note": jscpd_note,
        "commented_out": commented_out_code(),
        "window_globals": window_globals(),
        "todos": todo_count(),
        "large_files": large_files(),
        "gate_summary": [
            *boundary,
            *unused,
            *tid251,
            *dead,
            *deps,
        ],
        "measure_summary": [*complexity, *clones],
    }
    measures["fix_commands"] = fix_commands(measures)
    return measures


def cmd_weekly(args: argparse.Namespace) -> int:
    """Deep scan: measure, trend, and emit the report files."""
    measures = collect_measures()
    current = baseline_values(measures)
    baseline: dict[str, int] = {}
    if BASELINE_PATH.exists():
        try:
            baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            baseline = {}
    trend = trend_table(current, baseline)
    markdown = render_markdown(measures, trend)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "audit-report.md").write_text(markdown, encoding="utf-8")
    (out_dir / "audit-report.json").write_text(
        json.dumps({"measures": measures, "trend": trend}, indent=2), encoding="utf-8"
    )
    print(markdown)
    return 0


def cmd_fix() -> int:
    """Mechanical auto-fix: safe ruff fixes + formatting; print changed-file manifest.

    Only *tracked* modifications count as fixes (untracked files are never the
    auto-fix's doing) so the manifest maps 1:1 to the diff for the AUTO-BOT PR.
    """
    run(["ruff", "check", "--fix", "--select", "F401,F841", *SOURCE_DIRS, "tests"], timeout=180)
    run(["ruff", "format", *SOURCE_DIRS, "tests"], timeout=180)
    proc = run(["git", "status", "--porcelain"])
    changed = [
        line[3:] for line in proc.stdout.splitlines() if line.strip() and not line.startswith("??")
    ]
    if not changed:
        print("AUTOFIX_CLEAN")
        return 0
    for path in changed:
        print(f"CHANGED: {path}")
    return 0


def cmd_baseline() -> int:
    """(Re)generate the committed baseline file from current measurements."""
    measures = collect_measures()
    BASELINE_PATH.write_text(
        json.dumps(
            {"generated": datetime.now(UTC).isoformat(), **baseline_values(measures)}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Baseline written to {BASELINE_PATH.relative_to(ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weekly", action="store_true", help="full deep scan (report + trend)")
    parser.add_argument("--fix", action="store_true", help="mechanical auto-fix, print manifest")
    parser.add_argument("--baseline", action="store_true", help="(re)generate the baseline file")
    parser.add_argument(
        "--out-dir", default=".", help="directory for audit-report.* (default: cwd)"
    )
    args = parser.parse_args(argv)

    if args.fix:
        return cmd_fix()
    if args.baseline:
        return cmd_baseline()
    return cmd_weekly(args)


if __name__ == "__main__":
    sys.exit(main())
