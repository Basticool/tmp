"""Norm-based job management for multi-user mode.

A job assigns one or more norms to a user; the user labels every trace
for those norms. Progress is tracked per (sim_id, norm_id) work unit.

Simple mode uses norm-scoped label files directly (no job abstraction).
"""
from __future__ import annotations

import uuid
from pathlib import Path

from app.modules.storage import (
    append_jsonl,
    ensure_dir,
    now_iso,
    read_jsonl,
    write_jsonl,
)


# ── Multi-user job helpers ─────────────────────────────────────────────────────

def create_job(
    username: str,
    norm_ids: list[str],
    norm_traces: dict[str, list[dict]],
    jobs_dir: Path,
    sim_ids_filter: "dict[str, list[str]] | None" = None,
) -> str:
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    ensure_dir(jobs_dir)

    manifest_entry: dict = {
        "job_id": job_id,
        "username": username,
        "norm_ids": norm_ids,
        "created_at": now_iso(),
        "status": "pending",
    }
    if sim_ids_filter:
        manifest_entry["sim_ids_filter"] = sim_ids_filter
    append_jsonl(jobs_dir / "manifest.jsonl", manifest_entry)

    units = []
    for norm_id in norm_ids:
        allowed = set(sim_ids_filter[norm_id]) if sim_ids_filter and norm_id in sim_ids_filter else None
        for trace in norm_traces.get(norm_id, []):
            sim_id = trace.get("simulation", {}).get("id", "")
            if allowed is not None and sim_id not in allowed:
                continue
            units.append({
                "sim_id": sim_id,
                "norm_id": norm_id,
                "unit_status": "pending",
                "labeled_by": None,
                "labeled_at": None,
                "turns": [],
            })
    write_jsonl(jobs_dir / f"{job_id}_labels.jsonl", units)
    return job_id


def get_all_jobs(jobs_dir: Path) -> list[dict]:
    return read_jsonl(jobs_dir / "manifest.jsonl")


def get_user_jobs(username: str, jobs_dir: Path) -> list[dict]:
    return [j for j in get_all_jobs(jobs_dir) if j.get("username") == username]


def get_job_units(job_id: str, jobs_dir: Path) -> list[dict]:
    return read_jsonl(jobs_dir / f"{job_id}_labels.jsonl")


def save_unit_labels(
    job_id: str,
    sim_id: str,
    norm_id: str,
    turns: list[dict],
    username: str,
    jobs_dir: Path,
) -> None:
    labels_path = jobs_dir / f"{job_id}_labels.jsonl"
    units = get_job_units(job_id, jobs_dir)
    for unit in units:
        if unit["sim_id"] == sim_id and unit["norm_id"] == norm_id:
            unit["unit_status"] = "completed"
            unit["labeled_by"] = username
            unit["labeled_at"] = now_iso()
            unit["turns"] = turns
            break
    write_jsonl(labels_path, units)


def is_norm_complete_job(job_id: str, norm_id: str, jobs_dir: Path) -> bool:
    units = [
        u for u in get_job_units(job_id, jobs_dir)
        if u["norm_id"] == norm_id
    ]
    return bool(units) and all(u["unit_status"] == "completed" for u in units)


def get_completed_sim_ids_job(job_id: str, norm_id: str, jobs_dir: Path) -> set[str]:
    return {
        u["sim_id"]
        for u in get_job_units(job_id, jobs_dir)
        if u["norm_id"] == norm_id and u["unit_status"] == "completed"
    }


def delete_job(job_id: str, jobs_dir: Path) -> None:
    manifest = read_jsonl(jobs_dir / "manifest.jsonl")
    write_jsonl(
        jobs_dir / "manifest.jsonl",
        [j for j in manifest if j["job_id"] != job_id],
    )
    labels_path = jobs_dir / f"{job_id}_labels.jsonl"
    if labels_path.exists():
        labels_path.unlink()
    # Unclaim any bundle that was linked to this job
    bundles_path = jobs_dir / _BUNDLES_FILE
    bundles = read_jsonl(bundles_path)
    changed = any(b.get("job_id") == job_id for b in bundles)
    if changed:
        for b in bundles:
            if b.get("job_id") == job_id:
                b["claimed_by"] = None
                b["claimed_at"] = None
                b["job_id"] = None
        write_jsonl(bundles_path, bundles)


def update_job_status(job_id: str, jobs_dir: Path) -> None:
    """Recompute and write the job's top-level status from its unit statuses."""
    manifest = read_jsonl(jobs_dir / "manifest.jsonl")
    units = get_job_units(job_id, jobs_dir)
    total = len(units)
    done = sum(1 for u in units if u["unit_status"] == "completed")
    status = "completed" if done == total else ("pending" if done == 0 else "in_progress")
    for j in manifest:
        if j["job_id"] == job_id:
            j["status"] = status
            break
    write_jsonl(jobs_dir / "manifest.jsonl", manifest)


# ── Simple mode helpers ────────────────────────────────────────────────────────

def _norm_labels_path(labels_dir: Path, norm_id: str) -> Path:
    return labels_dir / f"{norm_id}.jsonl"


def get_simple_labels(labels_dir: Path, norm_id: str) -> list[dict]:
    return read_jsonl(_norm_labels_path(labels_dir, norm_id))


def get_completed_sim_ids_simple(labels_dir: Path, norm_id: str) -> set[str]:
    return {
        rec["sim_id"]
        for rec in get_simple_labels(labels_dir, norm_id)
        if rec.get("unit_status") == "completed"
    }


def save_simple_label(
    labels_dir: Path,
    norm_id: str,
    sim_id: str,
    turns: list[dict],
    labeled_by: str = "default",
) -> None:
    labels_path = _norm_labels_path(labels_dir, norm_id)
    existing = read_jsonl(labels_path)

    updated = False
    for rec in existing:
        if rec["sim_id"] == sim_id:
            rec["unit_status"] = "completed"
            rec["labeled_by"] = labeled_by
            rec["labeled_at"] = now_iso()
            rec["turns"] = turns
            updated = True
            break

    if not updated:
        existing.append({
            "sim_id": sim_id,
            "norm_id": norm_id,
            "unit_status": "completed",
            "labeled_by": labeled_by,
            "labeled_at": now_iso(),
            "turns": turns,
        })
    write_jsonl(labels_path, existing)


def is_norm_complete_simple(labels_dir: Path, norm_id: str, trace_count: int) -> bool:
    done = len(get_completed_sim_ids_simple(labels_dir, norm_id))
    return done >= trace_count


def cleanup_empty_and_completed_jobs(jobs_dir: Path) -> list[str]:
    """Delete jobs with no norms assigned. Returns deleted job IDs."""
    deleted: list[str] = []
    for job in get_all_jobs(jobs_dir):
        if not job.get("norm_ids"):
            delete_job(job["job_id"], jobs_dir)
            deleted.append(job["job_id"])
    return deleted


# ── Bundle helpers ─────────────────────────────────────────────────────────────

_BUNDLES_FILE = "bundles.jsonl"


def create_bundle(
    name: str,
    norm_ids: list[str],
    norm_traces: dict[str, list[dict]],
    jobs_dir: Path,
    sim_ids_filter: "dict[str, list[str]] | None" = None,
    eligible_labelers: "list[str] | None" = None,
    original_labeler: "str | None" = None,
) -> str:
    bundle_id = f"bundle_{uuid.uuid4().hex[:8]}"
    if sim_ids_filter:
        n_traces = sum(len(v) for v in sim_ids_filter.values())
    else:
        n_traces = sum(len(norm_traces.get(n, [])) for n in norm_ids)
    entry: dict = {
        "bundle_id": bundle_id,
        "name": name,
        "norm_ids": norm_ids,
        "n_traces": n_traces,
        "created_at": now_iso(),
        "claimed_by": None,
        "claimed_at": None,
        "job_id": None,
    }
    if sim_ids_filter:
        entry["sim_ids_filter"] = sim_ids_filter
    if eligible_labelers is not None:
        entry["eligible_labelers"] = eligible_labelers
    if original_labeler is not None:
        entry["original_labeler"] = original_labeler
    append_jsonl(jobs_dir / _BUNDLES_FILE, entry)
    return bundle_id


def get_all_bundles(jobs_dir: Path) -> list[dict]:
    return read_jsonl(jobs_dir / _BUNDLES_FILE)


def get_unclaimed_bundles(jobs_dir: Path) -> list[dict]:
    return [b for b in get_all_bundles(jobs_dir) if not b.get("claimed_by")]


def claim_bundle(
    bundle_id: str,
    username: str,
    norm_traces: dict[str, list[dict]],
    jobs_dir: Path,
) -> str:
    bundles = get_all_bundles(jobs_dir)
    job_id = None
    for bundle in bundles:
        if bundle["bundle_id"] == bundle_id:
            if bundle.get("claimed_by"):
                raise ValueError(f"Bundle already claimed by {bundle['claimed_by']}")
            sim_ids_filter = bundle.get("sim_ids_filter") or None
            job_id = create_job(username, bundle["norm_ids"], norm_traces, jobs_dir,
                                sim_ids_filter=sim_ids_filter)
            bundle["claimed_by"] = username
            bundle["claimed_at"] = now_iso()
            bundle["job_id"] = job_id
            break
    else:
        raise ValueError(f"Bundle {bundle_id} not found")
    write_jsonl(jobs_dir / _BUNDLES_FILE, bundles)
    return job_id


def get_job_sim_ids_filter_for_norm(username: str, norm_id: str, jobs_dir: Path) -> "list[str] | None":
    """Return the sim_ids filter list for a user's job containing norm_id, or None if no filter."""
    for job in get_user_jobs(username, jobs_dir):
        if norm_id in job.get("norm_ids", []):
            sim_filter = job.get("sim_ids_filter", {})
            return sim_filter.get(norm_id)
    return None


def delete_bundle(bundle_id: str, jobs_dir: Path) -> None:
    bundles = get_all_bundles(jobs_dir)
    write_jsonl(jobs_dir / _BUNDLES_FILE, [b for b in bundles if b["bundle_id"] != bundle_id])
