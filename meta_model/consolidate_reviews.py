#!/usr/bin/env python3
"""
Consolidate several per-dataset review results into ONE workbook, with each
distinct error listed ONCE together with every dataset it affects.

Typical use: a dataset family (e.g. four olive intensity variants, or a set of
crops from one provider) shares most of its errors, because the datasets were
built from the same template. Reviewing them one workbook at a time repeats
the same finding N times and hides the pattern. This groups them.

Output format matches the companion livestock-ecoinvent-check skill:
severity-ordered action list (Critical / High / Verify / Medium / Decision /
Low), an Activities overview for multi-dataset submissions, and a written
verdict.

Usage:
    python3 consolidate_reviews.py results_a.json results_b.json ... \
        --out /mnt/user-data/outputs/batch_review.xlsx \
        [--verdict /mnt/user-data/outputs/batch_verdict.md] \
        [--checklist assets/agri_internal_review_checklist.xlsx]

Each input is a results.json as written by run_all_checks.py and completed per
SKILL.md (Checks, agri related checks, hotspots, changes, flow_checks).
"dataset_name" identifies the dataset; add "dataset_label" for a short name.

Sheets written:
  Verdict               written verdict (resolved / must fix / decisions)
  Activities overview   one row per dataset: counts by severity, verdict
  Action_list           one row per checklist item failing in >=1 dataset,
                        with the affected datasets and per-dataset detail
  Specific items        hotspots grouped by (item, issue) across datasets
  Flow issues           flow_checks FAILs grouped by (check, flow)
  Checklist status      every checklist row x dataset, status codes only
  Changes Applied       all corrections, with a Dataset column

Grouping rule: two findings are "the same error" when they hit the same
checklist row (Action_list), the same item+issue (Specific items), or the
same check on the same flow (Flow issues). When every affected dataset has
identical detail text it is shown once; otherwise one line per dataset.
"""
import sys
import json
import argparse
from pathlib import Path
from collections import OrderedDict

from openpyxl import Workbook, load_workbook
from openpyxl.styles import PatternFill, Font, Alignment

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CHECKLIST = SCRIPT_DIR.parent / "assets" / "agri_internal_review_checklist.xlsx"

# Livestock-skill severity scale and colours, for a consistent team format.
SEV_ORDER = {"Critical": 0, "High": 1, "Verify": 2, "Medium": 3, "Decision": 4, "Low": 5}
SEV_FILL = {"Critical": "FFC7CE", "High": "FFD966", "Verify": "FFF2CC", "Medium": "FFF2CC",
            "Decision": "D9E1F2", "Low": "E7E6E6"}
STATUS_FILL = {"OK": "D9EAD3", "CORRECTED": "CFE2F3", "FAIL": "F4CCCC", "INFO": "FFF2CC"}

WRAP = Alignment(wrap_text=True, vertical="top")
BOLD = Font(bold=True)


def fill(hex_color):
    return PatternFill(start_color=hex_color, end_color=hex_color, fill_type="solid")


# ----------------------------------------------------------- normalisation

def infer_status(text):
    t = (text or "").strip().upper()
    if t.startswith("OK"):
        return "OK"
    if t.startswith("CORRECTED"):
        return "CORRECTED"
    if t.startswith("FAIL"):
        return "FAIL"
    return "INFO"


def norm_entry(value):
    """Checklist cell -> (status, comment, decision)."""
    if isinstance(value, dict):
        comment = value.get("comment", "") or ""
        return (value.get("status") or infer_status(comment), comment, value.get("decision"))
    comment = str(value)
    return infer_status(comment), comment, None


def checklist_severity(status, decision, explicit=None):
    """Map the checkdataset status/decision scheme onto the livestock scale.
    An explicit livestock-style severity on the entry always wins."""
    if explicit in SEV_ORDER:
        return explicit
    if status != "FAIL":
        return None
    if decision == "BLOCKING":
        return "Critical"
    if decision == "ACCEPTABLE_FOR_NOW":
        return "Medium"
    if decision == "DECISION":
        return "Decision"
    return "High"  # a FAIL nobody has triaged yet is treated as High, not hidden


def hotspot_severity(h):
    sev = h.get("severity")
    if sev in SEV_ORDER:
        return sev
    return {"BLOCKING": "Critical", "ACCEPTABLE_FOR_NOW": "Medium", "INFO": "Verify",
            "DECISION": "Decision"}.get((sev or "").upper())  # PASS/CORRECTED -> None


def squash(text):
    return " ".join(str(text or "").split())


def worst(sevs):
    sevs = [s for s in sevs if s]
    return min(sevs, key=lambda s: SEV_ORDER[s]) if sevs else None


def label_of(r, idx):
    return r.get("dataset_label") or r.get("dataset_name") or f"dataset {idx + 1}"


def assign_labels(results_list, mode):
    """'short': D1..Dn (legend in Verdict and Activities overview) - keeps the
    grouped 'Affected datasets' column readable. An explicit short
    'dataset_label' (<= 30 chars) set in a results file is always kept.
    'full': the dataset_label / dataset_name as-is."""
    out = []
    for i, r in enumerate(results_list):
        explicit = r.get("dataset_label_explicit") or None
        if mode == "full":
            out.append(label_of(r, i))
        elif explicit and len(explicit) <= 30:
            out.append(explicit)
        else:
            out.append(f"D{i + 1}")
    return out


def merged_detail(per_dataset):
    """per_dataset: OrderedDict label -> detail text. Identical detail across
    all affected datasets is shown once; otherwise one line per dataset."""
    texts = {squash(t) for t in per_dataset.values() if squash(t)}
    if len(texts) <= 1:
        t = next(iter(per_dataset.values()), "")
        return (f"Same in all {len(per_dataset)}: " if len(per_dataset) > 1 else "") + squash(t)
    return "\n".join(f"[{lbl}] {squash(t)}" for lbl, t in per_dataset.items())


# --------------------------------------------------------------- checklist

def load_checklist_text(checklist_path):
    """{(sheet, row): (area, check_text)} read live from the template."""
    out = {}
    wb = load_workbook(checklist_path, data_only=True)
    for sheet in ("Checks", "agri related checks"):
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        for r in range(2, ws.max_row + 1):
            vals = [ws.cell(row=r, column=c).value for c in range(1, 6)]
            if not any(vals):
                continue
            area = " / ".join(str(v) for v in vals[:3] if v)
            check = vals[4] or vals[2] or ""
            out[(sheet, r)] = (area, squash(check))
    return out


# ---------------------------------------------------------------- grouping

def build(results_list, checklist_path, label_mode="short"):
    labels = assign_labels(results_list, label_mode)
    checklist_text = load_checklist_text(checklist_path)

    # --- Action list: by checklist row ---
    action = OrderedDict()
    status_matrix = {}  # (sheet,row) -> {label: status}
    for r, lbl in zip(results_list, labels):
        for sheet in ("Checks", "agri related checks"):
            for row_str, value in (r.get(sheet) or {}).items():
                key = (sheet, int(row_str))
                status, comment, decision = norm_entry(value)
                explicit = value.get("severity") if isinstance(value, dict) else None
                status_matrix.setdefault(key, {})[lbl] = status
                sev = checklist_severity(status, decision, explicit)
                if not sev:
                    continue
                a = action.setdefault(key, {"sevs": [], "detail": OrderedDict()})
                a["sevs"].append(sev)
                a["detail"][lbl] = comment

    action_rows = []
    for (sheet, row), a in action.items():
        area, check = checklist_text.get((sheet, row), ("", ""))
        action_rows.append({
            "severity": worst(a["sevs"]),
            "ref": f"{'agri' if sheet != 'Checks' else 'Checks'} row {row}",
            "area": area, "check": check,
            "affected": list(a["detail"].keys()),
            "detail": merged_detail(a["detail"]),
        })

    # --- Specific items: hotspots by (item, issue) ---
    items = OrderedDict()
    for r, lbl in zip(results_list, labels):
        for h in r.get("hotspots") or []:
            sev = hotspot_severity(h)
            if not sev:
                continue
            key = (squash(h.get("exchange_name")), squash(h.get("issue")))
            it = items.setdefault(key, {"sevs": [], "detail": OrderedDict(),
                                        "ref": h.get("checklist_row", ""),
                                        "expected": OrderedDict()})
            it["sevs"].append(sev)
            it["expected"][lbl] = h.get("expected", "")
            d = squash(h.get("actual"))
            if h.get("note"):
                d += f" — {squash(h.get('note'))}"
            it["detail"][lbl] = d
    item_rows = [{
        "severity": worst(v["sevs"]), "item": k[0], "issue": k[1], "ref": v["ref"],
        "expected": merged_detail(v["expected"]).replace("Same in all ", "All ")
                    if len({squash(x) for x in v["expected"].values()}) <= 1
                    else merged_detail(v["expected"]),
        "affected": list(v["detail"].keys()),
        "detail": merged_detail(v["detail"]),
    } for k, v in items.items()]

    # --- Flow issues: flow_checks FAIL by (check, flow) ---
    flows = OrderedDict()
    for r, lbl in zip(results_list, labels):
        for f in r.get("flow_checks") or []:
            if (f.get("result") or "").upper() != "FAIL":
                continue
            key = (squash(f.get("check")), squash(f.get("flow_name")))
            fl = flows.setdefault(key, {"detail": OrderedDict(), "fixable": set()})
            fl["detail"][lbl] = f.get("comment", "")
            fl["fixable"].add(f.get("fixable"))
    flow_rows = [{
        "check": k[0], "flow": k[1], "affected": list(v["detail"].keys()),
        "fixable": "yes" if v["fixable"] == {True} else ("no" if True not in v["fixable"] else "mixed"),
        "detail": merged_detail(v["detail"]),
    } for k, v in flows.items()]

    # --- Changes ---
    change_rows = []
    for r, lbl in zip(results_list, labels):
        for c in r.get("changes") or []:
            change_rows.append({"dataset": lbl, **c})

    # --- Per-dataset overview ---
    overview = []
    for r, lbl in zip(results_list, labels):
        counts = {s: 0 for s in SEV_ORDER}
        for sheet in ("Checks", "agri related checks"):
            for value in (r.get(sheet) or {}).values():
                st, _, dec = norm_entry(value)
                sev = checklist_severity(st, dec, value.get("severity") if isinstance(value, dict) else None)
                if sev:
                    counts[sev] += 1
        n_corrected = sum(1 for sheet in ("Checks", "agri related checks")
                          for v in (r.get(sheet) or {}).values() if norm_entry(v)[0] == "CORRECTED")
        filled = sum(len(r.get(s) or {}) for s in ("Checks", "agri related checks"))
        if counts["Critical"]:
            verdict = "Blocked - critical issues"
        elif counts["High"] or counts["Decision"]:
            verdict = "Not ready - fixes/decisions needed"
        elif counts["Medium"] or counts["Verify"]:
            verdict = "Acceptable for this iteration"
        else:
            verdict = "Ready"
        overview.append({"dataset": lbl, "name": r.get("dataset_name", ""),
                         "file": r.get("dataset_label", ""), "counts": counts,
                         "corrected": n_corrected, "filled": filled, "verdict": verdict})

    def sort_key(x):
        return (SEV_ORDER.get(x["severity"], 9), -len(x["affected"]))

    action_rows.sort(key=sort_key)
    item_rows.sort(key=sort_key)
    flow_rows.sort(key=lambda x: (-len(x["affected"]), x["check"], x["flow"]))

    return {"labels": labels, "action": action_rows, "items": item_rows, "flows": flow_rows,
            "changes": change_rows, "overview": overview, "status_matrix": status_matrix,
            "checklist_text": checklist_text}


# ------------------------------------------------------------------ verdict

def verdict_text(data):
    labels = data["labels"]
    n = len(labels)
    shared = [a for a in data["action"] if len(a["affected"]) == n and n > 1]
    lines = [f"# Batch review verdict — {n} dataset(s)", ""]
    lines.append("## Datasets")
    for o in data["overview"]:
        c = o["counts"]
        lines.append(f"- **{o['dataset']}** = {o['name']} — {o['verdict']} "
                     f"(Critical {c['Critical']}, High {c['High']}, Decision {c['Decision']}, "
                     f"Medium {c['Medium']}; {o['corrected']} auto-corrected)")
    lines.append("")
    if shared:
        lines.append(f"## Common to all {n} datasets ({len(shared)} items) — fix once, at the template/source")
        for a in shared[:15]:
            lines.append(f"- [{a['severity']}] {a['ref']}: {a['check'][:110]}")
        if len(shared) > 15:
            lines.append(f"- ... and {len(shared) - 15} more (see Action_list)")
        lines.append("")
    must = [a for a in data["action"] if a["severity"] in ("Critical", "High")]
    lines.append(f"## Must fix ({len(must)} items)")
    for a in must[:20]:
        lines.append(f"- [{a['severity']}] {a['ref']} — {len(a['affected'])}/{n} datasets: {a['check'][:100]}")
    if len(must) > 20:
        lines.append(f"- ... and {len(must) - 20} more (see Action_list)")
    lines.append("")
    dec = [a for a in data["action"] if a["severity"] == "Decision"]
    lines.append(f"## Decisions needed ({len(dec)})")
    for a in dec:
        lines.append(f"- {a['ref']} — {len(a['affected'])}/{n}: {a['check'][:100]}")
    lines.append("")
    lines.append(f"## Resolved — {len(data['changes'])} correction(s) applied (see Changes Applied)")
    return "\n".join(lines)


# ------------------------------------------------------------------- writer

def _header(ws, headers, widths):
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = BOLD
        c.alignment = WRAP
        ws.column_dimensions[c.column_letter].width = widths[i - 1]
    ws.freeze_panes = "A2"


def _row(ws, values, sev=None, sev_col=None):
    ws.append(values)
    r = ws.max_row
    for c in range(1, len(values) + 1):
        ws.cell(row=r, column=c).alignment = WRAP
    if sev and sev_col:
        ws.cell(row=r, column=sev_col).fill = fill(SEV_FILL.get(sev, "FFFFFF"))


def affected_str(affected, n):
    return f"{len(affected)}/{n}: " + ", ".join(affected)


def write_workbook(data, out_path):
    n = len(data["labels"])
    wb = Workbook()

    ws = wb.active
    ws.title = "Verdict"
    ws.column_dimensions["A"].width = 140
    for line in verdict_text(data).splitlines():
        ws.append([line.replace("**", "")])
        if line.startswith("#"):
            ws.cell(row=ws.max_row, column=1).font = BOLD

    ws = wb.create_sheet("Activities overview")
    _header(ws, ["Label", "Activity name", "Source file", "Verdict", "Critical", "High", "Verify",
                 "Medium", "Decision", "Low", "Auto-corrected", "Checklist rows filled"],
            [8, 50, 40, 30, 9, 9, 9, 9, 9, 9, 13, 12])
    for o in data["overview"]:
        c = o["counts"]
        _row(ws, [o["dataset"], o["name"], o["file"], o["verdict"], c["Critical"], c["High"], c["Verify"],
                  c["Medium"], c["Decision"], c["Low"], o["corrected"], o["filled"]])
        v = ws.cell(row=ws.max_row, column=4)
        v.fill = fill(SEV_FILL["Critical"] if "Blocked" in o["verdict"] else
                      SEV_FILL["High"] if "Not ready" in o["verdict"] else "D9EAD3")

    ws = wb.create_sheet("Action_list")
    _header(ws, ["#", "Severity", "Checklist ref", "Area", "Check", "Affected datasets",
                 "Detail (per dataset when it differs)"],
            [5, 10, 14, 26, 50, 30, 80])
    for i, a in enumerate(data["action"], 1):
        _row(ws, [i, a["severity"], a["ref"], a["area"], a["check"],
                  affected_str(a["affected"], n), a["detail"]], a["severity"], 2)

    ws = wb.create_sheet("Specific items")
    _header(ws, ["#", "Severity", "Item / exchange", "Issue", "Checklist ref", "Expected",
                 "Affected datasets", "Found (per dataset when it differs)"],
            [5, 10, 28, 30, 16, 30, 30, 70])
    for i, it in enumerate(data["items"], 1):
        _row(ws, [i, it["severity"], it["item"], it["issue"], it["ref"], it["expected"],
                  affected_str(it["affected"], n), it["detail"]], it["severity"], 2)

    ws = wb.create_sheet("Flow issues")
    _header(ws, ["#", "Check", "Flow", "Auto-fixable", "Affected datasets",
                 "Detail (per dataset when it differs)"],
            [5, 32, 36, 11, 30, 80])
    for i, f in enumerate(data["flows"], 1):
        _row(ws, [i, f["check"], f["flow"], f["fixable"], affected_str(f["affected"], n), f["detail"]])

    ws = wb.create_sheet("Checklist status")
    _header(ws, ["Checklist ref", "Check"] + data["labels"], [14, 60] + [14] * n)
    for (sheet, row) in sorted(data["checklist_text"],
                               key=lambda k: (0 if k[0] == "Checks" else 1, k[1])):
        statuses = data["status_matrix"].get((sheet, row), {})
        ref = f"{'agri' if sheet != 'Checks' else 'Checks'} row {row}"
        _row(ws, [ref, data["checklist_text"][(sheet, row)][1]] +
             [statuses.get(lbl, "—") for lbl in data["labels"]])
        for j, lbl in enumerate(data["labels"], 3):
            st = statuses.get(lbl)
            if st in STATUS_FILL:
                ws.cell(row=ws.max_row, column=j).fill = fill(STATUS_FILL[st])

    ws = wb.create_sheet("Changes Applied")
    _header(ws, ["Dataset", "Field / Exchange", "Old value", "New value", "Checklist row", "Reason"],
            [22, 30, 40, 40, 14, 40])
    for c in data["changes"]:
        _row(ws, [c["dataset"], c.get("field", ""), c.get("old_value") or "(empty / not present)",
                  c.get("new_value", ""), c.get("checklist_row", ""), c.get("reason", "")])
        ws.cell(row=ws.max_row, column=3).fill = fill("FCE5CD")
        ws.cell(row=ws.max_row, column=4).fill = fill("D9EAD3")

    wb.save(out_path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", nargs="+", help="per-dataset results.json files")
    ap.add_argument("--out", required=True, help="consolidated workbook path")
    ap.add_argument("--verdict", default=None, help="also write the written verdict as markdown")
    ap.add_argument("--checklist", default=str(DEFAULT_CHECKLIST))
    ap.add_argument("--labels", choices=["short", "full"], default="short",
                    help="short: D1..Dn with a legend (default); full: dataset names")
    args = ap.parse_args()

    results_list = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.results]
    labels = assign_labels(results_list, args.labels)
    if len(set(labels)) != len(labels):
        sys.exit("Duplicate dataset labels - set a unique 'dataset_label_explicit' in each results.json")

    data = build(results_list, args.checklist, args.labels)
    write_workbook(data, args.out)
    text = verdict_text(data)
    if args.verdict:
        Path(args.verdict).write_text(text, encoding="utf-8")
    print(text)
    print(f"\nWrote {args.out}: {len(data['action'])} grouped checklist items, "
          f"{len(data['items'])} specific items, {len(data['flows'])} flow issues, "
          f"{len(data['changes'])} changes across {len(labels)} datasets.")


if __name__ == "__main__":
    main()
