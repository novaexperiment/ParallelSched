"""Web interface for ParallelSched.

Run it by double-clicking "Run Scheduler.command" (macOS) or "Run Scheduler.bat"
(Windows), or from a terminal with:  streamlit run app.py

Everything above main() is pure data-shuffling between the config dict and the
tables the UI shows, kept free of Streamlit calls so it can be tested directly.
"""

import ast
import hashlib
import os
import re

import pandas as pd
import streamlit as st

import ParallelSched
import schema

NO_RESTRICTION = "(any slot)"

SECTIONS = ["Layout", "Requests", "Conflicts", "Slot restrictions",
            "Previous agenda", "Solve", "Adjust & check"]


# --------------------------------------------------------------------------
# Config <-> table conversions (pure)
# --------------------------------------------------------------------------

def groups_to_df(cfg):
    rows = [{"Group": g, "Sessions": n} for g, n in cfg["group_sessions"].items()]
    return pd.DataFrame(rows, columns=["Group", "Sessions"])


def df_to_groups(df):
    out = {}
    for _, row in df.iterrows():
        name = str(row.get("Group") or "").strip()
        if not name:
            continue
        try:
            count = int(row.get("Sessions") or 1)
        except (TypeError, ValueError):
            count = 1
        out[name] = max(1, count)
    return out


def all_group_names(cfg):
    """Every name mentioned anywhere, not just groups that currently want sessions.

    Configs legitimately carry constraints about groups that are not meeting this
    time ('Computing' in summer2026). The engine ignores them, but the UI must
    still offer them as options or saving would quietly delete the constraint.
    """
    names = set(cfg["group_sessions"])
    for pair in cfg["strict_non_overlaps"]:
        names.update(pair)
    for group, conflicts in cfg["prioritized_non_overlaps"].items():
        names.add(group)
        names.update(conflicts)
    for joint in cfg["joint_sessions"]:
        names.update(joint)
    names.update(cfg["allowed_slots"])
    names.update(cfg["impossible_slots"])
    return sorted(n for n in names if n)


def strict_to_df(cfg):
    rows = [{"Group A": a, "Group B": b} for a, b in cfg["strict_non_overlaps"]]
    return pd.DataFrame(rows, columns=["Group A", "Group B"])


def df_to_strict(df):
    out = []
    for _, row in df.iterrows():
        a = str(row.get("Group A") or "").strip()
        b = str(row.get("Group B") or "").strip()
        if a and b and a != b and [a, b] not in out and [b, a] not in out:
            out.append([a, b])
    return out


def soft_to_df(cfg):
    """One row per (group, avoided group) pair, with priority made explicit.

    The engine weights the first entry in each group's list 10x the second and
    100x the third, so the ordering is the most consequential thing on the page.
    A flat table with a visible Priority number makes that editable and obvious,
    where a multiselect would hide it.
    """
    rows = []
    for group, conflicts in cfg["prioritized_non_overlaps"].items():
        for position, other in enumerate(conflicts, start=1):
            rows.append({"Group": group, "Avoid sharing a slot with": other,
                         "Priority": position})
    return pd.DataFrame(rows, columns=["Group", "Avoid sharing a slot with", "Priority"])


def df_to_soft(df):
    """Group the flat rows back into per-group ordered lists, low Priority first."""
    staged = {}
    for order, (_, row) in enumerate(df.iterrows()):
        group = str(row.get("Group") or "").strip()
        other = str(row.get("Avoid sharing a slot with") or "").strip()
        if not group or not other or group == other:
            continue
        try:
            priority = int(row.get("Priority") or 99)
        except (TypeError, ValueError):
            priority = 99
        staged.setdefault(group, []).append((priority, order, other))

    out = {}
    for group, entries in staged.items():
        entries.sort()  # by priority, then by the order typed, for stable ties
        seen, ordered = set(), []
        for _, _, other in entries:
            if other not in seen:
                seen.add(other)
                ordered.append(other)
        out[group] = ordered
    return out


def soft_weights(cfg):
    """The actual penalty weight each soft conflict earns, for display."""
    weights = {}
    for group, conflicts in cfg["prioritized_non_overlaps"].items():
        total = len(conflicts)
        for i, other in enumerate(conflicts):
            weights[(group, other)] = 10 ** (total - i)
    return weights


def slots_to_df(cfg, key, slot_names):
    """A checkbox grid: one row per group, one boolean column per time slot."""
    selected = cfg[key]
    groups = list(cfg["group_sessions"])
    # Show any group that already carries a restriction, even if it is not
    # requesting sessions this time, so editing here cannot drop it.
    groups += [g for g in selected if g not in cfg["group_sessions"] and selected[g]]
    rows = []
    for group in groups:
        chosen = set(selected.get(group) or [])
        row = {"Group": group}
        for i, name in enumerate(slot_names, start=1):
            row[name] = i in chosen
        rows.append(row)
    return pd.DataFrame(rows, columns=["Group"] + list(slot_names))


def df_to_slots(df, slot_names):
    out = {}
    for _, row in df.iterrows():
        group = str(row.get("Group") or "").strip()
        if not group:
            continue
        picked = [i for i, name in enumerate(slot_names, start=1) if bool(row.get(name))]
        if picked:
            out[group] = picked
    return out


def parse_previous_agenda_block(text):
    """Read a pasted `previous_agenda = {...}` block (or a bare dict literal)."""
    text = (text or "").strip()
    if not text:
        return {}
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("Could not find a { ... } block in that text.")
    parsed = ast.literal_eval(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("That block is not a dictionary.")
    return {int(slot): [str(i).strip() for i in items] for slot, items in parsed.items()}


def agenda_to_df(solution, moved_by_slot, cfg, slot_names):
    """Slots down the side, parallel tracks across, '*' marking moved items."""
    rows = []
    for slot in range(1, cfg["num_sessions"] + 1):
        items = sorted(item.strip() for item in solution.get(slot, []))
        moved = set((moved_by_slot or {}).get(slot, []))
        row = {"Time slot": slot_names[slot - 1], "#": len(items)}
        for track in range(1, cfg["num_tracks"] + 1):
            label = ""
            if track <= len(items):
                label = items[track - 1] + (" *" if items[track - 1] in moved else "")
            row[f"Track {track}"] = label
        rows.append(row)
    columns = ["Time slot", "#"] + [f"Track {t}" for t in range(1, cfg["num_tracks"] + 1)]
    return pd.DataFrame(rows, columns=columns)


def agenda_to_text(agenda, slot_names, num_slots=None):
    """The agenda as plain text: a slot heading, its sessions one per line, blank
    line between slots. Meant for pasting into documents and spreadsheets, where
    a table copies badly but a column of lines does not."""
    blocks = []
    total = num_slots if num_slots is not None else max(agenda or {1: []})
    for slot in range(1, total + 1):
        items = sorted(item.strip() for item in agenda.get(slot, []))
        name = slot_names[slot - 1] if slot <= len(slot_names) else f"Slot {slot}"
        blocks.append("\n".join([name] + items))
    return "\n\n".join(blocks)


UNPLACED = "Not scheduled"


def draft_pool(cfg):
    """Every block that belongs on the agenda, as (token, label) pairs.

    A group asking for three sessions is three separate blocks, so they can be
    dragged independently. The drag widget identifies items by their text, so a
    group with more than one session gets numbered tokens ('ND #1', 'ND #2')
    while everything else keeps its plain label.
    """
    pairs = []
    for group, count in cfg["group_sessions"].items():
        for index in range(1, int(count) + 1):
            pairs.append((f"{group} #{index}" if count > 1 else group, group))
    for joint in cfg["joint_sessions"]:
        if len(joint) >= 2:
            label = ParallelSched.joint_label(joint)
            pairs.append((label, label))
    return pairs


def agenda_to_containers(agenda, cfg, slot_names):
    """Split an agenda into the drag widget's containers: the holding pen first.

    Anything on the agenda that the config no longer asks for still gets a block,
    rather than vanishing - it is exactly what the user needs to drag out.
    """
    spare = {}
    for token, label in draft_pool(cfg):
        spare.setdefault(label, []).append(token)

    containers = [{"header": UNPLACED, "items": []}]
    for slot in range(1, cfg["num_sessions"] + 1):
        items = []
        for entry in agenda.get(slot, []):
            label = str(entry).strip()
            if not label:
                continue
            items.append(spare[label].pop(0) if spare.get(label) else label)
        containers.append({"header": slot_names[slot - 1], "items": items})

    containers[0]["items"] = [token for tokens in spare.values() for token in tokens]
    return containers


def containers_to_agenda(containers, cfg):
    """The inverse: drag containers back to {slot: [label]}, numbering stripped."""
    label_of = dict(draft_pool(cfg))
    agenda = {}
    for slot, container in enumerate(containers):
        if slot == 0:  # the holding pen is not a time slot
            continue
        agenda[slot] = [label_of.get(token, token) for token in container["items"]]
    return agenda


def seed_draft(cfg, result):
    """What the editor starts from: the last solve, else the pinned agenda, else empty."""
    if result is not None and result.ok:
        return {slot: list(items) for slot, items in result.solution.items()}
    if cfg["previous_agenda"]:
        return {slot: list(items) for slot, items in cfg["previous_agenda"].items()}
    return {slot: [] for slot in range(1, cfg["num_sessions"] + 1)}


def capacity_summary(cfg):
    requested = sum(cfg["group_sessions"].values()) + len(cfg["joint_sessions"])
    capacity = cfg["num_sessions"] * cfg["num_tracks"]
    return requested, capacity


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------

def load_into_state(cfg, source_name=None):
    st.session_state.cfg = cfg
    st.session_state.source_name = source_name
    st.session_state.pop("result", None)
    # Slot-name boxes are per meeting; drop them so the newly opened meeting's
    # values show instead of the previous one's.
    for key in [k for k in st.session_state if str(k).startswith("slotname_")]:
        st.session_state.pop(key, None)


def signature_key(prefix, signature):
    """A widget key that changes exactly when the table's shape does.

    Streamlit caches a data_editor's state under its key. If the columns change
    but the key does not - which is what happens when slots get renamed - the
    stale state no longer matches the new columns and the grid renders empty.
    Deriving the key from the same signature that drives the rebuild keeps the
    two in lockstep.
    """
    digest = hashlib.md5(repr(signature).encode("utf-8")).hexdigest()[:8]
    return f"{prefix}_{digest}"


def sidebar():
    cfg = st.session_state.cfg
    st.sidebar.header("Meeting")

    files = schema.list_configs()
    labels = ["(new meeting)"] + files
    current = st.session_state.get("source_name")
    index = labels.index(current) if current in labels else 0
    choice = st.sidebar.selectbox("Open", labels, index=index,
                                  help="Meeting files live in the configs/ folder next to this app.")
    if choice != current and not (choice == "(new meeting)" and current is None):
        if choice == "(new meeting)":
            load_into_state(schema.blank_config())
        else:
            load_into_state(schema.load(os.path.join(schema.CONFIG_DIR, choice)), choice)
        st.rerun()

    st.sidebar.divider()
    filename = st.sidebar.text_input(
        "Save as", value=st.session_state.get("source_name") or f"{cfg['name']}.yaml")
    if st.sidebar.button("Save meeting", width="stretch"):
        if not filename.endswith((".yaml", ".yml")):
            filename += ".yaml"
        path = schema.save(cfg, os.path.join(schema.CONFIG_DIR, filename))
        st.session_state.source_name = filename
        st.sidebar.success(f"Saved to configs/{os.path.basename(path)}")

    st.sidebar.divider()
    st.sidebar.caption("Advanced")
    with st.sidebar.expander("Solver settings"):
        cfg["weights"]["balance"] = st.number_input(
            "Balance weight", min_value=0, value=int(cfg["weights"].get("balance", 10)),
            help="How hard to push for an equal number of parallel sessions in every slot.")
        cfg["weights"]["similarity"] = st.number_input(
            "Similarity weight", min_value=0, value=int(cfg["weights"].get("similarity", 1)),
            help="How hard to push for staying close to the previous agenda.")
        cfg["solver"]["max_seconds"] = st.number_input(
            "Time limit (seconds)", min_value=1, value=int(cfg["solver"].get("max_seconds", 60)))
        st.caption(f"Random seed: {cfg['solver'].get('random_seed', 0)} — the same seed "
                   f"always gives the same agenda.")


def tab_setup(cfg, slot_names):
    cfg["name"] = st.text_input("Meeting name", value=cfg["name"])

    col1, col2 = st.columns(2)
    cfg["num_sessions"] = col1.number_input(
        "Time slots", min_value=1, max_value=40, value=int(cfg["num_sessions"]),
        help="How many separate times parallel sessions can run.")
    cfg["num_tracks"] = col2.number_input(
        "Parallel tracks", min_value=1, max_value=20, value=int(cfg["num_tracks"]),
        help="How many sessions can run at the same time.")

    requested, capacity = capacity_summary(cfg)
    col1, col2, col3 = st.columns(3)
    col1.metric("Sessions requested", requested)
    col2.metric("Places available", capacity)
    col3.metric("Spare", capacity - requested,
                delta_color="normal" if capacity >= requested else "inverse")
    if requested > capacity:
        st.error(f"{requested - capacity} too many sessions to fit. Add a slot or a track, "
                 f"or ask for fewer sessions on the Requests page.")

    st.subheader("Slot names")
    st.caption("Optional, but worth doing: naming slots 'Mon PM', 'Tue AM' makes every "
               "other tab readable. Without names you are picking 'Slot 5' blind.")
    # Plain text boxes rather than a grid: a handful of short strings does not need
    # a table, they tab through in order, and a keyed text_input cannot lose an
    # edit the way a data_editor fed from its own output can.
    labels = list(cfg.get("slot_labels") or [])
    per_row = 4
    new_labels = []
    for start in range(0, cfg["num_sessions"], per_row):
        columns = st.columns(per_row)
        for offset in range(per_row):
            index = start + offset
            if index >= cfg["num_sessions"]:
                break
            key = f"slotname_{index}"
            if key not in st.session_state:
                st.session_state[key] = labels[index] if index < len(labels) else ""
            new_labels.append(columns[offset].text_input(
                f"Slot {index + 1}", key=key, placeholder="e.g. Mon PM").strip())
    cfg["slot_labels"] = new_labels

    # Names reflect any slot labels just typed above, so the pickers read correctly
    # on the same run rather than one interaction late.
    current_slot_names = schema.slot_display_names(cfg)

    st.subheader("Slot groups")
    st.caption(
        "**Optional.** A slot group is a named set of time slots — for example "
        "**am** meaning every morning slot. Once you have named one, the Slot "
        "restrictions page can apply it to many groups in one click, instead of "
        "you ticking the same boxes over and over. Skip this if you would rather "
        "tick boxes directly.")

    slots_available = list(range(1, cfg["num_sessions"] + 1))

    def name_of(slot):
        return current_slot_names[slot - 1] if slot <= len(current_slot_names) else f"Slot {slot}"

    updated = {}
    for index, (name, slots) in enumerate(list(cfg["slot_groups"].items())):
        columns = st.columns([3, 8, 2])
        new_name = columns[0].text_input(
            "Name", value=name, key=f"sgname_{index}",
            placeholder="e.g. am", label_visibility="visible" if index == 0 else "collapsed")
        picked = columns[1].multiselect(
            "Slots in this group", options=slots_available,
            default=[s for s in slots if s in slots_available],
            format_func=name_of, key=f"sgslots_{index}",
            label_visibility="visible" if index == 0 else "collapsed")
        columns[2].write("")
        if columns[2].button("Remove", key=f"sgrm_{index}"):
            # Widget keys are positional, so clear them or the row below shifts up
            # and inherits this row's text.
            for stale in [k for k in st.session_state if str(k).startswith("sg")]:
                st.session_state.pop(stale, None)
            cfg["slot_groups"] = {n: s for n, s in cfg["slot_groups"].items() if n != name}
            st.rerun()
        if new_name.strip():
            updated[new_name.strip()] = sorted(picked)
    cfg["slot_groups"] = updated

    if st.button("Add a slot group"):
        candidate, suffix = "new group", 1
        while candidate in updated:
            suffix += 1
            candidate = f"new group {suffix}"
        cfg["slot_groups"] = dict(updated, **{candidate: []})
        st.rerun()


def tab_groups(cfg):
    st.caption("Every group that wants one or more sessions. The group name here is what "
               "every other tab refers to, so spelling matters.")
    edited = st.data_editor(
        groups_to_df(cfg),
        hide_index=True, num_rows="dynamic", width="content",
        column_config={
            "Group": st.column_config.TextColumn(required=True, width="medium"),
            # Labelled "Count" so the header fits a narrow column; the underlying
            # field stays "Sessions" for df_to_groups.
            "Sessions": st.column_config.NumberColumn(
                "Count", min_value=1, max_value=cfg["num_sessions"], step=1,
                default=1, width="small",
                help="How many sessions this group is asking for. A group cannot "
                     "have two in the same time slot."),
        },
        key="groups_editor")
    cfg["group_sessions"] = df_to_groups(edited)

    st.subheader("Joint sessions")
    st.caption("A session shared by several groups. It is scheduled in addition to those "
               "groups' own sessions, and never at the same time as them.")
    names = list(cfg["group_sessions"])
    joints = [list(j) for j in cfg["joint_sessions"]]
    keep = []
    for i, joint in enumerate(joints):
        cols = st.columns([10, 1])
        # Members not in the group list are kept as options so stale joints survive.
        options = sorted(set(names) | set(joint))
        picked = cols[0].multiselect(f"Joint session {i + 1}", options=options,
                                     default=joint, key=f"joint_{i}")
        if not cols[1].button("Remove", key=f"rm_joint_{i}"):
            # Half-filled rows are kept so the row does not vanish while the user
            # is still choosing; to_solver_kwargs drops anything under two members.
            keep.append(picked)
            if 0 < len(picked) < 2:
                st.warning("A joint session needs at least two groups.")
    cfg["joint_sessions"] = keep
    if st.button("Add joint session"):
        cfg["joint_sessions"] = keep + [[]]
        st.rerun()


def tab_conflicts(cfg):
    names = all_group_names(cfg)
    if not names:
        st.info("Add some groups first.")
        return

    st.subheader("Never at the same time")
    st.caption("Hard rule. If these cannot all be satisfied the solver reports it as "
               "impossible rather than compromising.")
    edited_strict = st.data_editor(
        strict_to_df(cfg),
        hide_index=True, num_rows="dynamic", width="content",
        column_config={
            "Group A": st.column_config.SelectboxColumn(options=names, width="medium"),
            "Group B": st.column_config.SelectboxColumn(options=names, width="medium"),
        },
        key="strict_editor")
    cfg["strict_non_overlaps"] = df_to_strict(edited_strict)

    st.subheader("Prefer not at the same time")
    st.caption("Soft rule, and **priority matters a lot**: within one group, priority 1 "
               "counts ten times as much as priority 2, and a hundred times as much as "
               "priority 3. Set 1 for the clash you least want.")
    edited_soft = st.data_editor(
        soft_to_df(cfg),
        hide_index=True, num_rows="dynamic", width="content",
        column_config={
            "Group": st.column_config.SelectboxColumn(options=names, width="medium"),
            "Avoid sharing a slot with": st.column_config.SelectboxColumn(
                options=names, width="medium"),
            "Priority": st.column_config.NumberColumn(
                min_value=1, max_value=20, step=1, default=1, width="small",
                help="1 = most important. Ties keep the order you typed them."),
        },
        key="soft_editor")
    cfg["prioritized_non_overlaps"] = df_to_soft(edited_soft)

    weights = soft_weights(cfg)
    if weights:
        with st.expander("What those priorities actually weigh"):
            rows = [{"Group": g, "Avoid": o, "Penalty weight": w}
                    for (g, o), w in sorted(weights.items(), key=lambda kv: -kv[1])]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="content")
            st.caption("Higher weight means the solver works harder to avoid that overlap. "
                       "Weights come from each group's list length and position.")


def quick_fill(cfg, slot_names):
    """Apply a named slot group to several groups at once.

    This is the point of slot groups: without it they are just a list nobody can
    use. Ticking 'am' for five groups by hand is twenty clicks.
    """
    if not cfg["slot_groups"]:
        st.info("Tip: define a **slot group** on the Layout page (say 'am' for the "
                "morning slots) and you can then apply it to several groups at once "
                "from here, rather than ticking the same boxes repeatedly.")
        return

    with st.expander("Quick fill from a slot group"):
        usable = {n: s for n, s in cfg["slot_groups"].items() if s}
        if not usable:
            st.caption("Your slot groups have no slots chosen yet. Set them on the Layout page.")
            return

        st.caption("Sets the whole row for each group you pick, replacing whatever "
                   "is ticked for it now.")
        columns = st.columns([3, 6, 3])
        chosen = columns[0].selectbox(
            "Slot group", sorted(usable),
            format_func=lambda n: f"{n} ({len(usable[n])} slots)")
        targets = columns[1].multiselect("Apply to groups", sorted(cfg["group_sessions"]))
        action = columns[2].selectbox("As", ["Restrict to slots", "Block slots"])

        slots = usable[chosen]
        readable = ", ".join(slot_names[s - 1] if s <= len(slot_names) else f"Slot {s}"
                             for s in slots)
        st.caption(f"**{chosen}** = {readable}")

        if st.button("Apply", disabled=not targets):
            key = "allowed_slots" if action.startswith("Restrict") else "impossible_slots"
            for group in targets:
                cfg[key][group] = list(slots)
            st.success(f"Applied '{chosen}' to {len(targets)} group(s).")
            st.rerun()


def tab_slots(cfg, slot_names):
    if not cfg["group_sessions"]:
        st.info("Add some groups first.")
        return

    # These grids have a column per slot and a row per group, so they must be
    # rebuilt when either changes - but not merely because a box was ticked.
    grid_signature = (tuple(slot_names), tuple(cfg["group_sessions"]))

    quick_fill(cfg, slot_names)

    st.subheader("Restrict to slots")
    st.caption("**Leave a row entirely unticked to mean 'any slot'.** Ticking boxes is a "
               "hard restriction: the group can *only* use the ticked slots. Tick fewer "
               "slots than the group has sessions and the schedule becomes impossible.")
    allowed = st.data_editor(
        slots_to_df(cfg, "allowed_slots", slot_names),
        hide_index=True, width="stretch", disabled=["Group"],
        column_config={"Group": st.column_config.TextColumn(width="medium")},
        key=signature_key("allowed_editor", grid_signature))
    cfg["allowed_slots"] = df_to_slots(allowed, slot_names)

    st.subheader("Block slots")
    st.caption("Slots this group cannot use. Tick nothing to leave it free.")
    blocked = st.data_editor(
        slots_to_df(cfg, "impossible_slots", slot_names),
        hide_index=True, width="stretch", disabled=["Group"],
        column_config={"Group": st.column_config.TextColumn(width="medium")},
        key=signature_key("blocked_editor", grid_signature))
    cfg["impossible_slots"] = df_to_slots(blocked, slot_names)


def tab_previous(cfg, slot_names):
    st.caption("When a previous agenda is set, the solver keeps the new one as close to it "
               "as it can, so late changes do not reshuffle everything.")
    if cfg["previous_agenda"]:
        rows = [{"Time slot": slot_names[s - 1] if s <= len(slot_names) else f"Slot {s}",
                 "Sessions": ", ".join(sorted(items))}
                for s, items in sorted(cfg["previous_agenda"].items())]
        st.dataframe(
            pd.DataFrame(rows), hide_index=True, width="stretch",
            column_config={"Time slot": st.column_config.TextColumn(width="medium")})
        with st.expander("Plain text (for documents and spreadsheets)"):
            st.caption("One slot heading per block, its sessions one per line. Use the "
                       "copy button at the top right of the box.")
            st.code(agenda_to_text(cfg["previous_agenda"], slot_names,
                                   max(cfg["num_sessions"], *cfg["previous_agenda"])),
                    language=None)
        if st.button("Clear previous agenda"):
            cfg["previous_agenda"] = {}
            st.rerun()
    else:
        st.info("No previous agenda set. Solve, then use 'Pin as previous agenda'.")

    with st.expander("Paste from a Python config"):
        st.caption("Accepts the `previous_agenda = { ... }` block the command-line "
                   "scripts print, so the two ways of working stay interchangeable.")
        pasted = st.text_area("Paste here", height=160, key="paste_prev")
        if st.button("Load pasted agenda"):
            try:
                cfg["previous_agenda"] = parse_previous_agenda_block(pasted)
                st.success(f"Loaded {len(cfg['previous_agenda'])} slots.")
                st.rerun()
            except (ValueError, SyntaxError) as exc:
                st.error(f"Could not read that: {exc}")


def render_result(cfg, result, slot_names):
    if result.errors:
        st.error("This cannot be scheduled as set up:")
        for message in result.errors:
            st.markdown(f"- {message}")
        return

    if not result.ok:
        st.error("No schedule can satisfy all the hard rules.")
        if result.conflicts:
            st.markdown("**These rules cannot all hold at once. Relax any one of them:**")
            for message in result.conflicts:
                st.markdown(f"- {message}")
            st.caption("This is the smallest set of clashing rules found, so every one "
                       "listed is genuinely involved.")
        else:
            st.caption(f"Solver status: {result.status}. Try loosening slot restrictions "
                       f"or strict conflicts.")
        return

    cols = st.columns(3)
    cols[0].metric("Changes vs previous",
                   result.num_changes if result.num_changes is not None else "n/a")
    cols[1].metric("Solve time", f"{result.wall_time * 1000:.0f} ms")
    cols[2].metric("Status", result.status.title())

    # "content" rather than "stretch": stretching spreads a one-digit count across
    # a wide column, which is exactly what makes these tables hard to read.
    st.dataframe(
        agenda_to_df(result.solution, result.moved, cfg, slot_names),
        hide_index=True, width="content",
        column_config={
            "Time slot": st.column_config.TextColumn(width="medium"),
            "#": st.column_config.NumberColumn(
                "#", width="small", help="Sessions running in this slot."),
        })
    if cfg["previous_agenda"]:
        st.caption("`*` marks a session that moved compared to the previous agenda.")

    with st.expander("Plain text (for documents and spreadsheets)"):
        st.caption("One slot heading per block, its sessions one per line. Use the copy "
                   "button at the top right of the box.")
        st.code(agenda_to_text(result.solution, slot_names, cfg["num_sessions"]),
                language=None)

    action = st.columns(2)
    if action[0].button("Pin as previous agenda", width="stretch",
                        help="Use this result as the baseline, then tweak a rule and "
                             "re-solve to see the smallest possible change."):
        cfg["previous_agenda"] = {s: sorted(i.strip() for i in items)
                                 for s, items in result.solution.items()}
        # Keep the schedule on screen. Only its "what moved" markers are stale
        # once this becomes the baseline, and those are cheap to recompute - the
        # page used to throw the whole result away instead, which meant pinning a
        # draft lost the very agenda you had just decided to keep.
        result.num_changes = ParallelSched.calculate_changes(
            result.solution, cfg["previous_agenda"])
        result.moved = ParallelSched.compute_moved(result.solution, cfg["previous_agenda"])
        st.rerun()
    if action[1].button("Try another arrangement", width="stretch",
                        help="Same rules, different equally-good answer."):
        cfg["solver"]["random_seed"] = int(cfg["solver"].get("random_seed", 0)) + 1
        st.session_state.pop("result", None)
        st.rerun()

    with st.expander("Copy-paste block for the Python configs"):
        st.code(ParallelSched.format_previous_agenda(result.solution), language="python")


def tab_solve(cfg, slot_names):
    if not cfg["group_sessions"]:
        st.info("Add some groups first.")
        return

    if st.button("Build the schedule", type="primary", width="stretch"):
        with st.spinner("Solving..."):
            st.session_state.result = ParallelSched.solve(**schema.to_solver_kwargs(cfg))

    for issue in schema.schema_warnings(cfg):
        st.warning(issue)

    result = st.session_state.get("result")
    if result is None:
        st.caption("Nothing solved yet.")
        return

    for message in result.warnings:
        st.warning(message)
    render_result(cfg, result, slot_names)

# The drag component is optional on purpose. It is a small third-party package,
# and losing it should cost the drag handles and nothing else - so any import
# failure at all (not installed, broken build, changed API) falls through to the
# dropdown grid below, which needs nothing beyond Streamlit itself.
try:
    from streamlit_sortables import sort_items
except Exception:  # pragma: no cover - depends on what is installed
    sort_items = None


# The blocks render inside a component iframe, which inherits none of
# Streamlit's theming. Painting an explicit light panel keeps the text legible
# whichever theme the page itself is using.
SORTABLE_STYLE = """
.sortable-component {
    display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-start;
    background: #f4f4f7; border-radius: 10px; padding: 10px;
    font-family: "Source Sans Pro", sans-serif;
}
.sortable-container {
    background: #e7e7ee; border-radius: 8px; padding: 6px;
}
/* Fixed basis, no growing: with more slots than fit on one row the containers
   wrap, and stretching would leave the last row's columns twice the width of
   the first's. Matching the component's own selector exactly, because its
   `.sortable-component.vertical .sortable-container { flex-grow: 1 }` outranks
   a plain `.sortable-container` of ours. */
.sortable-component.vertical .sortable-container {
    flex: 0 0 168px;
    /* Stop a full holding pen from stretching every empty slot to match it. */
    align-self: flex-start;
}
.sortable-container-header {
    font-weight: 600; font-size: 0.78rem; color: #26262e;
    padding: 2px 4px 6px 4px; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis;
}
/* Roomy enough to be an easy drop target while empty. */
.sortable-container-body { min-height: 58px; }
.sortable-item {
    background: #ffffff; color: #26262e; border: 1px solid #c9c9d4;
    border-radius: 6px; padding: 5px 8px; margin: 4px 0;
    font-size: 0.8rem; cursor: grab; text-align: center;
    /* Wrap rather than truncate: a joint session's label is the list of its
       groups, and 'Joint DetSyst + TB + ...' tells you nothing about which
       joint session you are looking at. */
    white-space: normal; overflow-wrap: anywhere;
}
"""


def grid_editor(cfg, slot_names, draft, key):
    """Dropdown-per-cell fallback: the agenda table, but every cell is editable."""
    labels = sorted({label for _, label in draft_pool(cfg)})
    rows = []
    for slot in range(1, cfg["num_sessions"] + 1):
        items = [str(i).strip() for i in draft.get(slot, []) if str(i).strip()]
        row = {"Time slot": slot_names[slot - 1]}
        for track in range(1, cfg["num_tracks"] + 1):
            row[f"Track {track}"] = items[track - 1] if track <= len(items) else ""
        rows.append(row)
    columns = ["Time slot"] + [f"Track {t}" for t in range(1, cfg["num_tracks"] + 1)]

    edited = st.data_editor(
        pd.DataFrame(rows, columns=columns), hide_index=True, width="stretch",
        disabled=["Time slot"], key=key,
        column_config={
            "Time slot": st.column_config.TextColumn(width="medium"),
            **{f"Track {t}": st.column_config.SelectboxColumn(
                f"Track {t}", options=[""] + labels, required=False)
               for t in range(1, cfg["num_tracks"] + 1)},
        })

    agenda = {}
    for index, (_, row) in enumerate(edited.iterrows(), start=1):
        placed = []
        for track in range(1, cfg["num_tracks"] + 1):
            value = row.get(f"Track {track}")
            if value and str(value).strip():
                placed.append(str(value).strip())
        agenda[index] = placed
    return agenda


def render_check(cfg, report, draft, slot_names):
    """Show what the hand-made agenda breaks, worst first."""
    placed = sum(len(items) for items in draft.values())
    wanted = sum(cfg["group_sessions"].values()) + len(
        [j for j in cfg["joint_sessions"] if len(j) >= 2])

    cols = st.columns(4)
    cols[0].metric("Rules broken", len(report.violations))
    cols[1].metric("Soft conflicts", len(report.soft_violations))
    cols[2].metric("Sessions placed", f"{placed} / {wanted}")
    cols[3].metric("Changes vs previous",
                   report.num_changes if report.num_changes is not None else "n/a")

    if report.ok:
        st.success("This agenda breaks no hard rule. The solver could have produced it.")
    else:
        st.error(f"{len(report.violations)} thing(s) to fix:")
        for message in report.violations:
            st.markdown(f"- {message}")

    if report.soft_violations:
        with st.expander(f"{len(report.soft_violations)} soft conflict(s) - allowed, "
                         f"but the solver would avoid them if it could"):
            for message, weight in report.soft_violations:
                st.markdown(f"- {message}")
            st.caption("Listed worst first. These do not make the agenda invalid.")

    for message in report.warnings:
        st.info(message)

    st.dataframe(
        agenda_to_df(draft, report.moved, cfg, slot_names),
        hide_index=True, width="content",
        column_config={
            "Time slot": st.column_config.TextColumn(width="medium"),
            "#": st.column_config.NumberColumn(
                "#", width="small", help="Sessions running in this slot."),
        })
    if cfg["previous_agenda"]:
        st.caption("`*` marks a session that moved compared to the previous agenda.")

    with st.expander("Plain text (for documents and spreadsheets)"):
        st.code(agenda_to_text(draft, slot_names, cfg["num_sessions"]), language=None)


def tab_adjust(cfg, slot_names):
    if not cfg["group_sessions"]:
        st.info("Add some groups first.")
        return

    st.caption("Move sessions by hand and see immediately which rules that breaks. "
               "Nothing here changes the config - it is a scratch pad until you "
               "pin it or hand it back to the solver.")

    # Re-seeding has to remount the editor widget, which otherwise keeps showing
    # its own copy of the old agenda; bumping the generation changes its key.
    if "draft" not in st.session_state:
        st.session_state.draft = seed_draft(cfg, st.session_state.get("result"))
        st.session_state.draft_gen = 0

    start = st.columns(3)
    if start[0].button("Start from the solved schedule", width="stretch",
                       disabled=not (st.session_state.get("result") is not None
                                     and st.session_state.result.ok),
                       help="Copy the last result from the Solve page."):
        st.session_state.draft = seed_draft(cfg, st.session_state.result)
        st.session_state.draft_gen += 1
        st.rerun()
    if start[1].button("Start from the previous agenda", width="stretch",
                       disabled=not cfg["previous_agenda"]):
        st.session_state.draft = {slot: list(items)
                                  for slot, items in cfg["previous_agenda"].items()}
        st.session_state.draft_gen += 1
        st.rerun()
    if start[2].button("Clear the agenda", width="stretch",
                       help="Empty every slot and start placing sessions yourself."):
        st.session_state.draft = {slot: [] for slot in range(1, cfg["num_sessions"] + 1)}
        st.session_state.draft_gen += 1
        st.rerun()

    draft = st.session_state.draft
    # The widget must also remount when the meeting itself changes shape, or it
    # would keep offering blocks for groups that no longer exist.
    shape = (tuple(slot_names), tuple(sorted(cfg["group_sessions"].items())),
             tuple(tuple(j) for j in cfg["joint_sessions"]),
             st.session_state.draft_gen)
    key = signature_key("draft_editor", shape)

    if sort_items is None:
        st.caption("Pick a session in any cell to place it. Blank a cell to take it out. "
                   "(Drag-and-drop is unavailable: `streamlit-sortables` is not installed.)")
        draft = grid_editor(cfg, slot_names, draft, key)
    else:
        st.caption("Drag a session from one slot to another. Drop it in "
                   f"**{UNPLACED}** to take it off the agenda.")
        containers = sort_items(
            agenda_to_containers(draft, cfg, slot_names), multi_containers=True,
            direction="vertical", custom_style=SORTABLE_STYLE, key=key)
        draft = containers_to_agenda(containers, cfg)

    st.session_state.draft = draft

    report = ParallelSched.check_agenda(draft, slot_names=slot_names,
                                        **schema.to_check_kwargs(cfg))
    render_check(cfg, report, draft, slot_names)

    action = st.columns(2)
    if action[0].button("Pin as previous agenda", width="stretch", key="pin_draft",
                        help="Make this the baseline the solver stays close to."):
        cfg["previous_agenda"] = {slot: sorted(i.strip() for i in items)
                                  for slot, items in draft.items() if items}
        st.rerun()
    if action[1].button("Let the solver finish it", width="stretch",
                        help="Solve with this draft as the baseline, so the solver keeps "
                             "what you placed and sorts out the rest."):
        cfg["previous_agenda"] = {slot: sorted(i.strip() for i in items)
                                  for slot, items in draft.items() if items}
        with st.spinner("Solving..."):
            st.session_state.result = ParallelSched.solve(**schema.to_solver_kwargs(cfg))
        st.session_state.goto = "Solve"
        st.rerun()

def main():
    st.set_page_config(page_title="Parallel session scheduler", page_icon="📅",
                       layout="wide")
    if "cfg" not in st.session_state:
        newest = schema.most_recent_config()
        if newest:
            load_into_state(schema.load(os.path.join(schema.CONFIG_DIR, newest)), newest)
        else:
            load_into_state(schema.blank_config())

    st.title("Parallel session scheduler")
    sidebar()

    cfg = st.session_state.cfg
    slot_names = schema.slot_display_names(cfg)

    # A segmented control rather than st.tabs, and it is not cosmetic: a
    # data_editor mounted inside a hidden tab panel never measures its own width,
    # so the checkbox grids render as one squashed column until a scroll forces a
    # re-layout. This looks like a tab strip but draws the section directly, so
    # every widget is visible at mount.
    # A segmented control deselects when you click the segment you are already on,
    # reporting None. Repairing that here - before the widget is instantiated, which
    # is the only point Streamlit allows its state to be set - keeps both the page
    # and the highlight correct within the same run, so re-clicking is a no-op.
    # Seeding session_state rather than passing default= avoids Streamlit's
    # "created with a default value but also had its value set" warning.
    # A page can ask to send the user elsewhere - "Let the solver finish it"
    # hands off to Solve. It cannot set `nav` itself: by the time a page runs the
    # nav widget already exists, and Streamlit refuses writes to a live widget's
    # state. So the request is parked and spent here, before the widget is built.
    if "goto" in st.session_state:
        st.session_state.nav = st.session_state.pop("goto")

    if st.session_state.get("nav") is None:
        st.session_state.nav = st.session_state.get("nav_last") or SECTIONS[0]

    picked = st.segmented_control(
        "Section", SECTIONS, label_visibility="collapsed", key="nav")
    section = picked or st.session_state.get("nav_last") or SECTIONS[0]
    st.session_state.nav_last = section

    if section == "Layout":
        tab_setup(cfg, slot_names)
    elif section == "Requests":
        tab_groups(cfg)
    elif section == "Conflicts":
        tab_conflicts(cfg)
    elif section == "Slot restrictions":
        tab_slots(cfg, slot_names)
    elif section == "Previous agenda":
        tab_previous(cfg, slot_names)
    elif section == "Solve":
        tab_solve(cfg, slot_names)
    elif section == "Adjust & check":
        tab_adjust(cfg, slot_names)


if __name__ == "__main__":
    main()
