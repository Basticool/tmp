"""Admin panel — multi-user mode only.

Tabs
----
Users       : add / remove labeler accounts.
Allocate    : assign norms to users (creates a job per assignment).
Jobs        : view all jobs, inspect progress, delete jobs.
Bundles     : create/assign bundles, including overlap bundles for IAA study.
"""
from __future__ import annotations

import streamlit as st

from app.config import JOBS_DIR, USERS_FILE
from app.modules.job_manager import (
    claim_bundle,
    create_bundle,
    create_job,
    delete_bundle,
    delete_job,
    get_all_bundles,
    get_all_jobs,
    get_completed_sim_ids_job,
    get_job_units,
    update_job_status,
)
from app.modules.storage import append_jsonl, clear_cache, now_iso, read_jsonl, write_jsonl

# ── Overlap bundle plan ────────────────────────────────────────────────────────
# (name, norm_id, n_take, original_labeler)
# sim_ids are resolved at creation time from the live norm_traces dict.
_OVERLAP_PLAN = [
    ("overlap-01  N0-cancel",            "N0-cancel",            12, "Louise"),
    ("overlap-02  N0-exchange",           "N0-exchange",          12, "Louise"),
    ("overlap-03  N0-return",             "N0-return",            12, "Louise"),
    ("overlap-04  N0-modify-order-items", "N0-modify-order-items",12, "Louise"),
    ("overlap-05  N1-cancel",             "N1-cancel",            12, "Louise"),
    ("overlap-06  N3-return",             "N3-return",            12, "leif"),
    ("overlap-07  N2-cancel",             "N2-cancel",            12, "Bastien"),
    ("overlap-08  glm_cancel",            "glm_cancel",           12, "Bastien"),
    ("overlap-09  glm_cancel+return",     "glm_cancel+return",    12, "leif"),
    ("overlap-10  glm_mod_user_addr",     "glm_mod_user_addr",    12, "Anuj"),
]

# Users never assigned (regardless of eligibility)
_OVERLAP_EXCLUDE = {"Louise", "batch1", "batch5", "admin"}

# Same physical person — treated as one for eligibility purposes
_SAME_PERSON: list[tuple[str, frozenset[str]]] = [
    ("Bastien", frozenset({"Bastien", "bastien", "Basti"})),
]
_CANONICAL  = {a: c for c, aliases in _SAME_PERSON for a in aliases}
_ALIAS_GROUP = {a: aliases for c, aliases in _SAME_PERSON for a in aliases}


def _sim_labeler_from_jobs() -> dict[str, str]:
    """Build {sim_id: labeled_by} from all completed non-v8 job units."""
    result: dict[str, str] = {}
    for job in get_all_jobs(JOBS_DIR):
        for unit in get_job_units(job["job_id"], JOBS_DIR):
            sid = unit.get("sim_id", "")
            if (
                unit.get("unit_status") == "completed"
                and unit.get("labeled_by")
                and not sid.startswith("v8e_")
            ):
                result[sid] = unit["labeled_by"]
    return result


def _compute_overlap_eligible(
    sim_ids: list[str],
    original_labeler: str,
    all_users: list[str],
    sim_labeler: dict[str, str],
) -> list[str]:
    """Return canonical eligible usernames for an overlap bundle."""
    sim_set = set(sim_ids)
    orig_aliases = _ALIAS_GROUP.get(original_labeler, frozenset({original_labeler}))
    excluded = _OVERLAP_EXCLUDE | orig_aliases
    seen: set[str] = set()
    eligible: list[str] = []
    for user in sorted(all_users):
        if user in excluded:
            continue
        person_aliases = _ALIAS_GROUP.get(user, frozenset({user}))
        if any(sim_labeler.get(s) in person_aliases for s in sim_set):
            continue
        canon = _CANONICAL.get(user, user)
        if canon not in seen:
            seen.add(canon)
            eligible.append(canon)
    return eligible


# ── User helpers ───────────────────────────────────────────────────────────────

def _load_users() -> list[str]:
    return [r["username"] for r in read_jsonl(USERS_FILE)]


def _add_user(username: str) -> str | None:
    users = _load_users()
    if username in users:
        return f"User **{username}** already exists."
    append_jsonl(USERS_FILE, {"username": username, "created_at": now_iso()})
    return None


def _remove_user(username: str) -> str | None:
    if username == "admin":
        return "Cannot remove admin."
    jobs = [j for j in get_all_jobs(JOBS_DIR) if j["username"] == username and j["status"] != "completed"]
    if jobs:
        return f"User **{username}** has {len(jobs)} active job(s). Delete them first."
    records = read_jsonl(USERS_FILE)
    write_jsonl(USERS_FILE, [r for r in records if r["username"] != username])
    return None


# ── Render ─────────────────────────────────────────────────────────────────────

def render() -> None:
    if st.session_state.get("username") != "admin":
        st.error("Admin access only.")
        return

    st.title("Admin")
    tab_users, tab_alloc, tab_jobs, tab_bundles = st.tabs(["Users", "Allocate Norms", "Jobs", "Bundles"])

    # ── Users tab ─────────────────────────────────────────────────────────────
    with tab_users:
        st.subheader("Manage users")
        users = _load_users()
        st.write(f"**{len(users)}** registered users: {', '.join(users)}")
        st.divider()

        col1, col2 = st.columns(2)
        with col1:
            new_user = st.text_input("New username").strip()
            if st.button("Add user", key="add_user"):
                if not new_user:
                    st.error("Enter a username.")
                elif err := _add_user(new_user):
                    st.error(err)
                else:
                    st.success(f"Added **{new_user}**.")
                    st.rerun()

        with col2:
            del_user = st.selectbox(
                "Remove user",
                [u for u in users if u != "admin"],
                key="del_user_sel",
            )
            if del_user and st.button("Remove user", key="rem_user"):
                if err := _remove_user(del_user):
                    st.error(err)
                else:
                    st.success(f"Removed **{del_user}**.")
                    st.rerun()

    # ── Allocate tab ──────────────────────────────────────────────────────────
    with tab_alloc:
        st.subheader("Assign norms to a user")
        norm_traces: dict = st.session_state["norm_traces"]
        norms_with_obs: set = st.session_state.get("norms_with_obs", set())
        available_norms = sorted(n for n in norm_traces if n in norms_with_obs)
        non_admin_users = [u for u in _load_users() if u != "admin"]

        if not non_admin_users:
            st.info("No labeler accounts yet. Create users in the Users tab.")
        else:
            target_user = st.selectbox("Assign to user", non_admin_users, key="alloc_user")

            # Show which norms already have a job for this user
            existing_jobs = get_all_jobs(JOBS_DIR)
            already_assigned: set[str] = set()
            for j in existing_jobs:
                if j["username"] == target_user:
                    already_assigned.update(j.get("norm_ids", []))

            to_assign = st.multiselect(
                "Norms to assign",
                [n for n in available_norms if n not in already_assigned],
                key="alloc_norms",
            )

            if to_assign:
                n_traces = sum(len(norm_traces.get(n, [])) for n in to_assign)
                st.caption(f"This will create a job with {n_traces} trace(s) total.")
            if st.button("Create job", disabled=not to_assign, key="create_job"):
                job_id = create_job(target_user, to_assign, norm_traces, JOBS_DIR)
                st.success(f"Job `{job_id}` created for **{target_user}**: {', '.join(to_assign)}")
                st.rerun()

            st.caption(
                "To create annotator overlap (same traces labeled by multiple people), "
                "assign the same norm(s) to more than one user."
            )

            if already_assigned:
                st.caption(f"Already assigned to **{target_user}**: {', '.join(sorted(already_assigned))}")

    # ── Jobs tab ──────────────────────────────────────────────────────────────
    with tab_jobs:
        st.subheader("All jobs")
        all_jobs = get_all_jobs(JOBS_DIR)
        if not all_jobs:
            st.info("No jobs yet.")
        else:
            for job in all_jobs:
                job_id = job["job_id"]
                units = get_job_units(job_id, JOBS_DIR)
                total_u = len(units)
                done_u = sum(1 for u in units if u["unit_status"] == "completed")
                update_job_status(job_id, JOBS_DIR)

                with st.expander(
                    f"`{job_id}` — {job['username']} | "
                    f"{done_u}/{total_u} units | {job.get('status', '?')}",
                    expanded=False,
                ):
                    st.write(f"**Norms:** {', '.join(job.get('norm_ids', []))}")
                    st.write(f"**Created:** {job.get('created_at', '?')}")

                    # Per-norm progress
                    for norm_id in job.get("norm_ids", []):
                        norm_units = [u for u in units if u["norm_id"] == norm_id]
                        n_done = sum(1 for u in norm_units if u["unit_status"] == "completed")
                        st.write(f"  • `{norm_id}`: {n_done}/{len(norm_units)}")

                    if st.button(f"Delete job {job_id}", key=f"del_{job_id}"):
                        delete_job(job_id, JOBS_DIR)
                        st.warning(f"Deleted job `{job_id}`.")
                        st.rerun()

    # ── Bundles tab ───────────────────────────────────────────────────────────
    with tab_bundles:
        st.subheader("Bundles")
        st.caption(
            "Bundles are pools of norms that any user can claim on login. "
            "Once claimed, the bundle is locked to that user and a job is created automatically."
        )

        if st.button("↺ Refresh from GitHub", key="refresh_bundles", help="Clear the in-process cache and reload all data from GitHub. Use this after an external git push."):
            clear_cache()
            st.rerun()

        norm_traces: dict = st.session_state["norm_traces"]
        norms_with_obs: set = st.session_state.get("norms_with_obs", set())
        allocatable_norms = sorted(n for n in norm_traces if n in norms_with_obs)

        with st.expander("Create new bundle", expanded=False):
            bundle_name = st.text_input("Bundle name (optional label for the admin)", key="bundle_name")
            bundle_norms = st.multiselect(
                "Norms to include",
                allocatable_norms,
                key="bundle_norms",
            )
            if bundle_norms:
                n_traces = sum(len(norm_traces.get(n, [])) for n in bundle_norms)
                st.caption(f"{len(bundle_norms)} norm(s) · {n_traces} trace(s)")
            if st.button("Create bundle", disabled=not bundle_norms, key="create_bundle"):
                bid = create_bundle(
                    bundle_name or f"Bundle {now_iso()[:10]}",
                    bundle_norms,
                    norm_traces,
                    JOBS_DIR,
                )
                st.success(f"Bundle `{bid}` created.")
                st.rerun()

        with st.expander("Create overlap bundles (inter-annotator agreement study)", expanded=True):
            st.caption(
                "Creates 10 bundles of 12 traces each, sampling from already human-labeled "
                "non-v8 norms. Each bundle shows eligible labelers (those who haven't labeled "
                "those traces before and are not Louise). Bastien / bastien / Basti are treated "
                "as one person."
            )
            existing_bundle_names = {b["name"] for b in get_all_bundles(JOBS_DIR)}
            pending = [p for p in _OVERLAP_PLAN if p[0] not in existing_bundle_names]

            if not pending:
                st.success("All 10 overlap bundles already exist. Assign them below.")
            else:
                st.write(f"**{len(pending)}** of 10 bundles not yet created:")
                for name, norm_id, n_take, orig in pending:
                    available = len(norm_traces.get(norm_id, []))
                    st.caption(f"  • {name}  ({norm_id}, {min(n_take, available)} traces, original: {orig})")

                if st.button("Create all missing overlap bundles", key="create_overlap", type="primary"):
                    all_users = _load_users()
                    sim_labeler = _sim_labeler_from_jobs()
                    created = 0
                    for name, norm_id, n_take, original_labeler in pending:
                        traces_for_norm = norm_traces.get(norm_id, [])
                        sim_ids = sorted(
                            t.get("simulation", {}).get("id", "")
                            for t in traces_for_norm
                            if not t.get("simulation", {}).get("id", "").startswith("v8e_")
                        )[:n_take]
                        eligible = _compute_overlap_eligible(
                            sim_ids, original_labeler, all_users, sim_labeler
                        )
                        create_bundle(
                            name,
                            [norm_id],
                            norm_traces,
                            JOBS_DIR,
                            sim_ids_filter={norm_id: sim_ids},
                            eligible_labelers=eligible,
                            original_labeler=original_labeler,
                        )
                        created += 1
                    st.success(f"Created {created} overlap bundle(s).")
                    st.rerun()

        st.divider()
        all_bundles = get_all_bundles(JOBS_DIR)
        if not all_bundles:
            st.info("No bundles yet.")
        else:
            for bundle in all_bundles:
                bid = bundle["bundle_id"]
                name = bundle.get("name", bid)
                n_traces = bundle.get("n_traces", "?")
                norms_list = ", ".join(bundle.get("norm_ids", []))
                claimed_by = bundle.get("claimed_by")
                status = f"claimed by **{claimed_by}**" if claimed_by else "unclaimed"
                with st.expander(f"`{name}` — {status} · {n_traces} traces", expanded=False):
                    st.write(f"**ID:** `{bid}`")
                    st.write(f"**Norms:** {norms_list}")
                    st.write(f"**Created:** {bundle.get('created_at', '?')[:10]}")
                    if claimed_by:
                        st.write(f"**Claimed by:** {claimed_by} at {bundle.get('claimed_at', '?')[:10]}")
                        st.write(f"**Job:** `{bundle.get('job_id', '?')}`")
                    else:
                        orig = bundle.get("original_labeler")
                        eligible = bundle.get("eligible_labelers")
                        if orig:
                            st.write(f"**Original labeler:** {orig}")
                        if eligible:
                            st.write(f"**Eligible labelers:** {', '.join(eligible)}")

                        non_admin_users = [u for u in _load_users() if u != "admin"]
                        assignable = eligible if eligible else non_admin_users
                        if assignable:
                            col_sel, col_btn, col_del = st.columns([2, 2, 1])
                            with col_sel:
                                sel_user = st.selectbox(
                                    "Assign to",
                                    assignable,
                                    key=f"assign_sel_{bid}",
                                    label_visibility="collapsed",
                                )
                            with col_btn:
                                if st.button(f"Assign to {sel_user}", key=f"assign_{bid}", type="primary"):
                                    try:
                                        claim_bundle(bid, sel_user, norm_traces, JOBS_DIR)
                                        st.success(f"Bundle `{name}` assigned to **{sel_user}**.")
                                        st.rerun()
                                    except ValueError as exc:
                                        st.error(str(exc))
                            with col_del:
                                if st.button("Delete", key=f"del_bundle_{bid}"):
                                    delete_bundle(bid, JOBS_DIR)
                                    st.warning(f"Deleted bundle `{bid}`.")
                                    st.rerun()
                        else:
                            if st.button(f"Delete bundle {bid}", key=f"del_bundle_{bid}"):
                                delete_bundle(bid, JOBS_DIR)
                                st.warning(f"Deleted bundle `{bid}`.")
                                st.rerun()
