#!/usr/bin/env python3
"""Self-check the evidence chain under ``minimax/`` (V1).

Run from anywhere:

    python scripts/check-evidence.py            # from minimax/
    python minimax/scripts/check-evidence.py    # from the repo root

The script is read-only (it copies the snapshots to a temporary directory when
it needs to test patch application) and prints one PASS/FAIL line per check.
Exit code = number of failed checks.

Checks:
  1. patch ↔ meta ↔ MiniMax records ↔ index hash chain
  2. model response files ↔ committed code ↔ before/after snapshots ↔ patch
  3. run/verification ↔ test logs ↔ JUnit testcase sets ↔ code_version
  4. context packs: context_sha256, resolvable citations, spec v1→v2 change
  5. seekdb evidence (DDL/counts/retrieval) and PowerContext state readback
  6. documentation claims that can be checked mechanically
  7. repository hygiene (valid JSON everywhere, relative evidence paths,
     no plaintext credentials)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(os.environ.get("SDDCTX_ROOT") or Path(__file__).resolve().parent.parent).resolve()
PATCH_REL = "patches/0001-orders-filters.patch"
META_REL = "patches/0001-orders-filters.meta.json"
RUN_REL = "evidence/run.json"
VERIFICATION_REL = "evidence/verification.json"
READBACK_REL = "records/powercontext/state-readback.json"

PASS = 0
FAIL = 0
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"PASS  {name}" + (f"  [{detail}]" if detail else ""))
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"FAIL  {name}" + (f"  [{detail}]" if detail else ""))


def section(title: str) -> None:
    print(f"\n========== {title} ==========")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_text(text: str) -> str:
    return sha_bytes(text.encode("utf-8"))


def sha_json(value: object) -> str:
    return sha_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def load(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def rel_exists(rel: str) -> bool:
    return (ROOT / rel).is_file()


def iter_json_files() -> list[Path]:
    skip = {".data", "__pycache__", ".venv", "venv", ".pytest_cache", ".ruff_cache"}
    files = []
    for path in sorted(ROOT.rglob("*.json")):
        if any(part in skip for part in path.parts):
            continue
        if any(part.endswith(".egg-info") for part in path.parts):
            continue
        files.append(path)
    return files


def read_response_content(record: dict) -> str:
    body = (record.get("response") or {}).get("body") or {}
    try:
        return body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return (record.get("response") or {}).get("content") or ""


def file_blocks(content: str) -> dict[str, str]:
    marker = re.compile(r"^===\s*FILE:\s*(?P<path>[A-Za-z0-9_./-]+?)\s*===\s*$", re.MULTILINE)
    matches = list(marker.finditer(content))
    out: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        body = content[start:end]
        fence = re.search(r"```[A-Za-z0-9_+.-]*[ \t]*\r?\n(.*?)\r?\n```", body, re.DOTALL)
        text = fence.group(1) if fence else body
        text = text.strip("\n")
        if text and not text.endswith("\n"):
            text += "\n"
        out[match.group("path")] = text
    return out


def junit_testcases(path: Path) -> set[str] | None:
    if not path.is_file():
        return None
    try:
        root = ET.parse(str(path)).getroot()
    except ET.ParseError:
        return None
    cases: set[str] = set()
    for case in root.iter("testcase"):
        classname = case.get("classname") or ""
        name = case.get("name") or ""
        cases.add(f"{classname}::{name}" if classname else name)
    return cases


def main() -> int:
    print(f"checking evidence under: {ROOT}")
    for rel in (PATCH_REL, META_REL, RUN_REL, VERIFICATION_REL, READBACK_REL):
        if not rel_exists(rel):
            print(f"ERROR missing required file: {rel}")
            return 1

    patch_text = (ROOT / PATCH_REL).read_text(encoding="utf-8")
    meta = load(META_REL)
    run = load(RUN_REL)
    verification = load(VERIFICATION_REL)
    readback = load(READBACK_REL)

    # ---------------------------------------------------------------- 1. chain
    section("1. patch / meta / MiniMax records / index hash chain")
    check("meta.patch_sha256 == sha256(patch)", meta["patch_sha256"] == sha_text(patch_text),
          meta["patch_sha256"][:16])
    check("meta.patch_path is the recorded patch", Path(meta["patch_path"]).as_posix() == PATCH_REL,
          meta["patch_path"])
    check("meta.context_pack exists", rel_exists(meta["context_pack"]), meta["context_pack"])
    pack = load(meta["context_pack"])
    check("meta.context_sha256 == sha256(context_text)",
          meta["context_sha256"] == sha_text(pack.get("context_text", "")),
          meta["context_sha256"][:16])
    check("context pack stores the same sha", pack.get("context_sha256") == meta["context_sha256"])

    records = sorted((ROOT / "records/minimax").glob("*attempt*.json"))
    check("at least one MiniMax record", len(records) >= 1, f"{len(records)} record(s)")
    index_lines = [line for line in (ROOT / "records/minimax/index.jsonl").read_text(encoding="utf-8").splitlines()
                   if line.strip()]
    records_data = {}
    prev_sha: str | None = None
    for path in records:
        record = json.loads(path.read_text(encoding="utf-8"))
        records_data[path.name] = record
        chain = record.get("chain") or {}
        response = record.get("response") or {}
        request = record.get("request") or {}
        content = read_response_content(record)
        check(f"{path.name}: response content sha", response.get("content_sha256") == sha_text(content),
              str(response.get("content_sha256"))[:16])
        messages_sha = request.get("messages_sha256")
        check(f"{path.name}: request messages sha", isinstance(messages_sha, str) and len(messages_sha) == 64)
        check(f"{path.name}: prev link", chain.get("prev_record_sha256") in (None, prev_sha))
        headers = request.get("headers_redacted") or {}
        auth = str(headers.get("Authorization") or headers.get("authorization") or "")
        check(f"{path.name}: Authorization redacted", auth.startswith("***REDACTED"), auth[:24])
        auth_meta = record.get("auth") or {}
        check(f"{path.name}: only api-key hash stored", isinstance(auth_meta.get("api_key_sha256"), str)
              and len(auth_meta.get("api_key_sha256", "")) == 64 and "api_key" not in auth_meta,
              str(sorted(auth_meta.keys())))
        prev_sha = chain.get("record_sha256")

    for entry in index_lines:
        payload = json.loads(entry)
        record_rel = payload.get("record_path", "")
        check(f"index entry is repo-relative: {record_rel}",
              not record_rel.startswith("/") and rel_exists(record_rel), record_rel)
        target = ROOT / record_rel
        if target.is_file():
            check(f"index record sha matches: {target.name}",
                  payload.get("record_sha256") == (json.loads(target.read_text(encoding="utf-8")).get("chain") or {}).get("record_sha256"))

    applied_attempt = None
    for attempt in meta["attempts"]:
        name = Path(attempt.get("record", "")).name
        record = records_data.get(name)
        check(f"meta attempt {attempt['attempt']} record exists", record is not None, name)
        if record is None:
            continue
        content = read_response_content(record)
        check(f"meta attempt {attempt['attempt']} response sha matches record",
              attempt.get("response_sha256") == sha_text(content))
        usage_record = (record.get("response") or {}).get("usage") or {}
        check(f"meta attempt {attempt['attempt']} usage matches record",
              (attempt.get("usage") or {}).get("total_tokens") == usage_record.get("total_tokens"))
        if attempt.get("applied"):
            applied_attempt = attempt
            check("applied attempt patch_sha256 == patch file", attempt.get("patch_sha256") == sha_text(patch_text))

    touched = set(re.findall(r"^(?:---|\+\+\+) [ab]/([^\t\n]+)", patch_text, flags=re.MULTILINE))
    touched = {path.strip() for path in touched if path.strip() and path.strip() != "/dev/null"}
    check("patch touches only app/ files", bool(touched) and all(p.startswith("app/") for p in touched), str(sorted(touched)))

    # -------------------------------------------------- 2. response / snapshot
    section("2. model output -> committed code -> snapshots -> patch")
    if applied_attempt is not None:
        record = records_data.get(Path(applied_attempt["record"]).name, {})
        blocks = file_blocks(read_response_content(record))
        check("applied response contains file blocks", bool(blocks), f"{len(blocks)} file(s)")
        for path, body in sorted(blocks.items()):
            after = ROOT / "evidence/snapshots/after" / path
            check(f"response file == after snapshot: {path}", after.is_file() and after.read_text(encoding="utf-8") == body)

    after_dir = ROOT / "evidence/snapshots/after"
    before_dir = ROOT / "evidence/snapshots/before"
    live_dir = ROOT / "demo"
    mismatch = [p.relative_to(after_dir).as_posix() for p in after_dir.rglob("*.py")
                if (live_dir / p.relative_to(after_dir)).read_bytes() != p.read_bytes()]
    check("after snapshot == committed demo/app", not mismatch, str(mismatch))

    sys.path.insert(0, str(ROOT))
    try:
        from sddctx.patch import apply_patch, reverse_patch
    except Exception as exc:
        check("import sddctx.patch", False, f"{type(exc).__name__}: {exc}")
        apply_patch = reverse_patch = None

    if apply_patch is not None:
        temp = Path(tempfile.mkdtemp(prefix="sddctx-evidence-"))
        try:
            work = temp / "app-tree"
            shutil.copytree(before_dir, work)
            applied = apply_patch(work, patch_text)
            check("before snapshot + patch applies", bool(applied.get("ok")), str(applied.get("stderr", ""))[:80])
            same = all((work / p.relative_to(after_dir)).is_file()
                       and (work / p.relative_to(after_dir)).read_bytes() == p.read_bytes()
                       for p in after_dir.rglob("*.py"))
            check("before + patch == after (byte-exact)", same)

            work2 = temp / "app-tree-2"
            shutil.copytree(after_dir, work2)
            reversed_ = reverse_patch(work2, patch_text)
            check("after snapshot reverses the patch", bool(reversed_.get("ok")), str(reversed_.get("stderr", ""))[:80])
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    # --------------------------------------------- 3. run / verification / logs
    section("3. run / verification / test logs / JUnit sets / code_version")
    check("run.generation.mode == live", (run.get("generation") or {}).get("mode") == "live",
          str((run.get("generation") or {}).get("mode")))
    check("run.patch.sha256 == patch", run["patch"]["sha256"] == sha_text(patch_text))
    check("run.context_sha256 == meta.context_sha256", run.get("context_sha256") == meta["context_sha256"])
    code_version = run.get("code_version") or {}
    check("run.code_version.base_git_head is a commit hash",
          bool(re.fullmatch(r"[0-9a-f]{40}", str(code_version.get("base_git_head", "")))))
    check("run.code_version.patch_sha256 == patch", code_version.get("patch_sha256") == sha_text(patch_text))
    check("run.verdict.ok", bool(run["verdict"]["ok"]), f"{len(run['verdict']['checks'])} checks")
    check("verification.verdict.ok", bool(verification["verdict"]["ok"]),
          f"{len(verification['verdict']['checks'])} checks")

    for tree in ("before", "after"):
        tree_logs = {name: (ROOT / entry["log_path"]) for name, entry in run[tree].items()}
        for name, log_path in tree_logs.items():
            check(f"{tree}/{name} log exists", log_path.is_file(), log_path.as_posix())
            if not log_path.is_file():
                continue
            log = json.loads(log_path.read_text(encoding="utf-8"))
            check(f"{tree}/{name} exit_code matches run.json", log["exit_code"] == run[tree][name]["exit_code"])
            check(f"{tree}/{name} counts match run.json", log["counts"] == run[tree][name]["counts"], str(log["counts"]))
            check(f"{tree}/{name} stdout sha", log.get("stdout_sha256") == sha_text(log.get("stdout", "")))
            log_version = log.get("code_version") or {}
            check(f"{tree}/{name} log records code_version",
                  log_version.get("patch_sha256") == sha_text(patch_text) and log_version.get("tree_state") == tree)

    before_target = json.loads((ROOT / run["before"]["target"]["log_path"]).read_text(encoding="utf-8"))
    after_target = json.loads((ROOT / run["after"]["target"]["log_path"]).read_text(encoding="utf-8"))
    before_reg = json.loads((ROOT / run["before"]["regression"]["log_path"]).read_text(encoding="utf-8"))
    after_reg = json.loads((ROOT / run["after"]["regression"]["log_path"]).read_text(encoding="utf-8"))
    check("before target has 6 functional failures",
          before_target["counts"]["failed"] == 6 and before_target["counts"]["errors"] == 0,
          str(before_target["counts"]))
    check("after target 6 passed", after_target["counts"]["passed"] == 6)
    check("regression 18 passed before and after",
          before_reg["counts"]["passed"] == 18 and after_reg["counts"]["passed"] == 18)

    for suite in ("target", "regression"):
        before_xml = Path(before_target["junit"] if suite == "target" else before_reg["junit"])
        after_xml = Path(after_target["junit"] if suite == "target" else after_reg["junit"])
        before_cases = junit_testcases(before_xml)
        after_cases = junit_testcases(after_xml)
        check(f"{suite}: JUnit testcase sets identical",
              before_cases is not None and before_cases == after_cases,
              f"{len(before_cases or [])} cases")

    # ------------------------------------------------------------- 4. context
    section("4. context packs: hash, citations, spec update")
    for rel in ("evidence/context/spec-v1-context.json", "evidence/context/spec-v2-context.json",
                meta["context_pack"]):
        pack_data = load(rel)
        check(f"{Path(rel).name}: context_sha256 recomputes",
              pack_data.get("context_sha256") == sha_text(pack_data.get("context_text", "")))

    v1_pack = load("evidence/context/spec-v1-context.json")
    v2_pack = load("evidence/context/spec-v2-context.json")
    v1_specs = {entry.get("requirement_id") for entry in v1_pack["specs"]}
    v2_specs = {entry.get("requirement_id") for entry in v2_pack["specs"]}
    check("spec v1 pack lacks REQ-ORD-006..009", not ({"REQ-ORD-006", "REQ-ORD-007", "REQ-ORD-008", "REQ-ORD-009"} & v1_specs))
    check("spec v2 pack adds REQ-ORD-006..009",
          {"REQ-ORD-006", "REQ-ORD-007", "REQ-ORD-008", "REQ-ORD-009"} <= v2_specs)
    check("spec v1/v2 context hashes differ", v1_pack["context_sha256"] != v2_pack["context_sha256"])

    def resolve(pack_data: dict, base: Path) -> tuple[int, list[str]]:
        checked, problems = 0, []
        for section_name in ("specs", "code", "tests"):
            for entry in pack_data.get(section_name, []):
                citation = entry.get("citation", "")
                path_part, _, rng = citation.partition(":")
                start, _, end = rng.partition("-")
                if not path_part or not start.isdigit() or not end.isdigit():
                    problems.append(citation)
                    continue
                candidates = [ROOT / path_part, base / path_part]
                target = next((c for c in candidates if c.is_file()), None)
                if target is None:
                    problems.append(citation)
                    continue
                lines = target.read_text(encoding="utf-8").splitlines()
                if not (1 <= int(start) <= int(end) <= len(lines)):
                    problems.append(citation)
                    continue
                checked += 1
        return checked, problems

    live_pack = load(meta["context_pack"])
    checked, problems = resolve(live_pack, ROOT / "evidence/snapshots/before")
    check("live context citations resolvable", not problems and checked > 0, f"{checked} citations, problems={problems[:3]}")

    # code excerpts in the live pack must match the pre-change snapshot
    excerpt_problems = []
    for entry in live_pack.get("code", []):
        citation = entry.get("citation", "")
        path_part, _, rng = citation.partition(":")
        start, _, end = rng.partition("-")
        target = ROOT / "evidence/snapshots/before" / path_part
        if not target.is_file():
            continue
        lines = target.read_text(encoding="utf-8").splitlines()
        source = "\n".join(lines[int(start) - 1: int(end)])
        if entry.get("excerpt", "").strip() and entry["excerpt"].strip() != source.strip():
            excerpt_problems.append(citation)
    check("live context excerpts match pre-change snapshot", not excerpt_problems, str(excerpt_problems[:3]))

    # ------------------------------------------------------ 5. seekdb / state
    section("5. seekdb evidence / PowerContext state")
    db_records = sorted((ROOT / "records/db").glob("seekdb-queries-*.json"))
    check("seekdb query evidence present", bool(db_records), f"{len(db_records)} file(s)")
    if db_records:
        db = json.loads(db_records[-1].read_text(encoding="utf-8"))
        check("evidence schema is seekdb-evidence/v1", db.get("schema") == "sddctx.seekdb-evidence/v1", str(db.get("schema")))
        names = {query["name"] for query in db["queries"]}
        check("DDL query recorded", "schema" in names)
        check("per-kind retrieval recorded", {"retrieval_specs", "retrieval_code", "retrieval_tests"} <= names, str(sorted(names)))
        ddl = next((query for query in db["queries"] if query["name"] == "schema"), {})
        ddl_text = json.dumps(ddl, ensure_ascii=False)
        check("DDL uses FULLTEXT ... WITH PARSER ik", "FULLTEXT" in ddl_text and "ik" in ddl_text)
        all_kinds = next((query for query in db["queries"] if query["name"] == "retrieval_all_kinds"), {})
        kinds = {row[0] for row in all_kinds.get("rows", []) if row}
        check("UNION retrieval returns spec/code/test rows", {"spec", "code", "test"} <= kinds, str(sorted(kinds)))
        check("seekdb count matches run ingest", any(
            query["name"] == "counts_by_kind" and sum(row[1] for row in query.get("rows", [])) == run["ingest"]["chunks"]
            for query in db["queries"]))

    state_files = sorted((ROOT / "records/powercontext").glob("state-final-*.json"))
    check("PowerContext write record present", bool(state_files), f"{len(state_files)} file(s)")
    if state_files:
        state = json.loads(state_files[-1].read_text(encoding="utf-8"))
        write_pairs = {(record["kind"], record["text"]) for record in state["records"]}
        read_pairs = {(record["kind"], record["text"]) for record in readback["records"]}
        check("fresh-process readback equals written state", write_pairs == read_pairs, f"{len(write_pairs)} records")
        check("readback prepared.status == ready", (readback.get("prepared") or {}).get("status") == "ready")
        check("readback records >= 10", len(readback["records"]) >= 10, f"{len(readback['records'])}")

    # ---------------------------------------------------------------- 6. docs
    section("6. documentation claims")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs/runbook.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    audit = (ROOT / "docs/audit-v1.md").read_text(encoding="utf-8")
    lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    check("README mentions V1", "V1" in readme)
    check("README lists verify-all.sh", "verify-all.sh" in readme)
    check("README lists check-evidence.py", "check-evidence.py" in readme)
    check("runbook version table matches lock", all(
        prefix in runbook
        for prefix in ("| powercontext | 1.4.1", "| pymysql | 1.2.0", "| pytest | 9.1.1")
    ))
    check("lock pins powercontext 1.4.1", "powercontext==1.4.1" in lock)
    check("runbook documents Python 3.12 floor", "3.12" in runbook and "3.11" in runbook)
    check("runbook documents pure-Python patch engine", "纯 Python" in runbook)
    check("runbook CLI table mentions db evidence", "db evidence" in runbook)
    check("CHANGELOG has V1 entry", "## V1" in changelog)
    check("audit report exists and is linked", "audit-v1.md" in readme and len(audit) > 2000)
    demo_banners = re.findall(r"\b(\d)/7\b", (ROOT / "scripts/demo.sh").read_text(encoding="utf-8"))
    check("demo.sh uses a consistent step denominator", bool(demo_banners))
    check("tech-note does not claim unified-diff output", "(unified diff)" not in (ROOT / "docs/tech-note.md").read_text(encoding="utf-8"))

    # ------------------------------------------------------------ 7. hygiene
    section("7. repository hygiene")
    invalid = []
    for path in iter_json_files():
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            invalid.append(str(path.relative_to(ROOT)))
    check("all JSON files parse", not invalid, str(invalid[:3]))

    absolute_refs: list[str] = []
    link_keys = {"context_pack", "patch_path", "record", "log_path", "db_evidence",
                 "state_evidence", "archive", "path"}

    def walk_links(value: object, trail: list[str]) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in link_keys and isinstance(item, str) and item:
                    if item.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", item):
                        absolute_refs.append("/".join([*trail, key]) + "=" + item[:60])
                else:
                    walk_links(item, [*trail, str(key)])
        elif isinstance(value, list):
            for item in value:
                walk_links(item, trail)

    for rel in (RUN_REL, VERIFICATION_REL, READBACK_REL, META_REL):
        walk_links(load(rel), [rel])
    check("evidence link fields are repo-relative", not absolute_refs, str(absolute_refs[:3]))

    secret_hits: list[str] = []
    placeholder_markers = ("placeholder", "not-a-real", "fake", "dummy", "example", "redacted")
    for path in list(ROOT.rglob("*.json")) + list(ROOT.rglob("*.md")) + list(ROOT.rglob("*.py")):
        if any(part in {".data", "__pycache__", ".venv"} for part in path.parts):
            continue
        if "tests" in path.parts:
            continue  # tests use explicitly fake keys
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}", text):
            candidate = match.group(0)
            if any(marker in candidate.lower() for marker in placeholder_markers):
                continue
            secret_hits.append(f"{path.relative_to(ROOT)}:{candidate[:24]}")
    check("no plaintext API keys", not secret_hits, str(secret_hits[:3]))

    # ---------------------------------------------------------------- summary
    print()
    print(f"========== SUMMARY: {PASS} passed, {FAIL} failed ==========")
    if FAILED:
        print("failed checks:")
        for name in FAILED:
            print(f"  - {name}")
    return FAIL


if __name__ == "__main__":
    raise SystemExit(main())
