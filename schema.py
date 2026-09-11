"""Meeting configuration: the YAML file format, and the bridge to the solver.

A config is a plain dict. Slot numbers are 1-based everywhere in this layer,
matching what the config scripts have always used; ParallelSched converts to
0-based internally via convert_values_to_0_based.

Two naming notes carried over deliberately:

* ``allowed_slots`` is what the scripts called ``preferences``. It is a *hard*
  whitelist, not a soft preference, so the name here says what it does. The old
  key is still accepted when loading.
* ``prioritized_non_overlaps`` is order-sensitive: the first entry in each list
  is weighted 10x the second, 100x the third, and so on. Reordering changes the
  outcome far more than most people expect.
"""

import copy
import os

import yaml

CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs")

# Written in this order so saved files read top-down like the old scripts.
KEY_ORDER = [
    "name", "num_sessions", "num_tracks", "slot_labels", "slot_groups",
    "group_sessions", "joint_sessions", "strict_non_overlaps",
    "prioritized_non_overlaps", "allowed_slots", "impossible_slots",
    "previous_agenda", "weights", "solver",
]

DEFAULT_WEIGHTS = {"balance": 10, "similarity": 1}
DEFAULT_SOLVER = {"random_seed": 0, "max_seconds": 60}


def blank_config(name="New meeting", num_sessions=6, num_tracks=4):
    return {
        "name": name,
        "num_sessions": num_sessions,
        "num_tracks": num_tracks,
        "slot_labels": [],
        "slot_groups": {},
        "group_sessions": {},
        "joint_sessions": [],
        "strict_non_overlaps": [],
        "prioritized_non_overlaps": {},
        "allowed_slots": {},
        "impossible_slots": {},
        "previous_agenda": {},
        "weights": dict(DEFAULT_WEIGHTS),
        "solver": dict(DEFAULT_SOLVER),
    }


def _resolve_slots(value, slot_groups):
    """A slot list may be written as ints, or as the name of a slot group.

    ``allowed_slots: {Exotics: am}`` with ``slot_groups: {am: [1, 2, 5, 6]}``
    keeps the readable shorthand the .py configs achieved with local variables.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return list(slot_groups.get(value, []))
    out = []
    for item in value:
        if isinstance(item, str):
            out.extend(slot_groups.get(item, []))
        elif isinstance(item, int):
            out.append(item)
    # de-duplicate, keep ascending order
    return sorted(set(out))


def normalize(raw):
    """Fill defaults, accept legacy key names, resolve slot-group shorthands."""
    cfg = blank_config()
    raw = dict(raw or {})

    # `preferences` was the old name for `allowed_slots`.
    if "allowed_slots" not in raw and "preferences" in raw:
        raw["allowed_slots"] = raw.pop("preferences")

    for key in ("name",):
        if raw.get(key):
            cfg[key] = str(raw[key])
    for key in ("num_sessions", "num_tracks"):
        if raw.get(key) is not None:
            cfg[key] = int(raw[key])

    cfg["slot_groups"] = {
        str(k): sorted({int(v) for v in (vs or [])})
        for k, vs in (raw.get("slot_groups") or {}).items()
    }
    slot_groups = cfg["slot_groups"]

    labels = list(raw.get("slot_labels") or [])
    cfg["slot_labels"] = [str(x) for x in labels]

    cfg["group_sessions"] = {
        str(k): int(v) for k, v in (raw.get("group_sessions") or {}).items()
    }
    cfg["joint_sessions"] = [
        [str(g) for g in joint] for joint in (raw.get("joint_sessions") or [])
    ]
    cfg["strict_non_overlaps"] = [
        [str(g) for g in pair] for pair in (raw.get("strict_non_overlaps") or [])
        if len(pair) == 2
    ]
    cfg["prioritized_non_overlaps"] = {
        str(k): [str(g) for g in (vs or [])]
        for k, vs in (raw.get("prioritized_non_overlaps") or {}).items()
    }
    cfg["allowed_slots"] = {
        str(k): _resolve_slots(v, slot_groups)
        for k, v in (raw.get("allowed_slots") or {}).items()
    }
    cfg["impossible_slots"] = {
        str(k): _resolve_slots(v, slot_groups)
        for k, v in (raw.get("impossible_slots") or {}).items()
    }
    cfg["previous_agenda"] = {
        int(slot): [str(i).strip() for i in (items or [])]
        for slot, items in (raw.get("previous_agenda") or {}).items()
    }

    cfg["weights"] = dict(DEFAULT_WEIGHTS)
    cfg["weights"].update(raw.get("weights") or {})
    cfg["solver"] = dict(DEFAULT_SOLVER)
    cfg["solver"].update(raw.get("solver") or {})

    return cfg


def schema_warnings(cfg):
    """Problems with the file itself, as opposed to the scheduling problem."""
    issues = []
    labels = cfg.get("slot_labels") or []
    if labels and len(labels) != cfg["num_sessions"]:
        issues.append(
            f"{len(labels)} slot label(s) given but {cfg['num_sessions']} time slots; "
            f"labels will be padded or truncated."
        )
    for name, slots in cfg["slot_groups"].items():
        bad = [s for s in slots if not 1 <= s <= cfg["num_sessions"]]
        if bad:
            issues.append(f"Slot group '{name}' includes slot(s) {bad} outside 1..{cfg['num_sessions']}.")
    for pair in cfg["strict_non_overlaps"]:
        if pair[0] == pair[1]:
            issues.append(f"Strict conflict pairs '{pair[0]}' with itself; ignored.")
    for group, conflicts in cfg["prioritized_non_overlaps"].items():
        if group in conflicts:
            issues.append(f"Soft conflict for '{group}' lists itself; ignored.")
        if len(set(conflicts)) != len(conflicts):
            issues.append(f"Soft conflict for '{group}' repeats a group; only the first position counts.")
    return issues


def slot_display_names(cfg):
    """Labels for the UI: the user's own where given, else 'Slot N'."""
    labels = list(cfg.get("slot_labels") or [])
    names = []
    for i in range(cfg["num_sessions"]):
        label = labels[i].strip() if i < len(labels) and labels[i].strip() else ""
        names.append(f"{i + 1}. {label}" if label else f"Slot {i + 1}")
    return names


def to_solver_kwargs(cfg):
    """Map a config onto ParallelSched.solve's signature."""
    return dict(
        group_sessions=dict(cfg["group_sessions"]),
        # A joint session needs two members to mean anything; the UI keeps
        # half-filled rows on screen while the user is still choosing.
        joint_sessions=[list(j) for j in cfg["joint_sessions"] if len(j) >= 2],
        strict_non_overlaps=[tuple(p) for p in cfg["strict_non_overlaps"]],
        prioritized_non_overlaps={k: list(v) for k, v in cfg["prioritized_non_overlaps"].items()},
        preferences={k: list(v) for k, v in cfg["allowed_slots"].items() if v},
        impossible_slots={k: list(v) for k, v in cfg["impossible_slots"].items() if v},
        num_sessions=cfg["num_sessions"],
        num_tracks=cfg["num_tracks"],
        previous_agenda=dict(cfg["previous_agenda"]) or None,
        balance_weight=cfg["weights"].get("balance", 10),
        similarity_weight=cfg["weights"].get("similarity", 1),
        random_seed=cfg["solver"].get("random_seed", 0),
        max_seconds=cfg["solver"].get("max_seconds", 60),
    )


def dumps(cfg):
    ordered = {k: cfg[k] for k in KEY_ORDER if k in cfg}
    return yaml.safe_dump(ordered, sort_keys=False, default_flow_style=None,
                          allow_unicode=True, width=100)


def load(path):
    with open(path, "r") as handle:
        return normalize(yaml.safe_load(handle))


def save(cfg, path):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w") as handle:
        handle.write(dumps(cfg))
    return path


def list_configs(directory=CONFIG_DIR):
    if not os.path.isdir(directory):
        return []
    names = [f for f in os.listdir(directory) if f.endswith((".yaml", ".yml"))]
    return sorted(names)


def most_recent_config(directory=CONFIG_DIR):
    """The meeting file saved most recently, or None if there are none.

    This is what the app opens on startup: whichever meeting you were last
    working on is nearly always the one you want next, and alphabetical order
    is not a useful guess about that.
    """
    names = list_configs(directory)
    if not names:
        return None

    def saved_at(name):
        try:
            return os.path.getmtime(os.path.join(directory, name))
        except OSError:
            return 0.0

    return max(names, key=saved_at)


def from_legacy_script(path):
    """Extract a config from one of the original .py meeting scripts.

    The scripts call schedule_sessions at import time, so we stub that call and
    capture its arguments rather than trying to parse the literals.
    """
    captured = {}

    def fake_schedule_sessions(group_sessions, joint_sessions, strict_non_overlaps,
                               prioritized_non_overlaps, preferences, impossible_slots,
                               num_sessions, num_tracks, previous_agenda=None, **_):
        captured.update(
            group_sessions=group_sessions,
            joint_sessions=joint_sessions,
            strict_non_overlaps=strict_non_overlaps,
            prioritized_non_overlaps=prioritized_non_overlaps,
            allowed_slots=preferences,
            impossible_slots=impossible_slots,
            num_sessions=num_sessions,
            num_tracks=num_tracks,
            previous_agenda=previous_agenda,
        )

    import ParallelSched
    real = ParallelSched.schedule_sessions
    ParallelSched.schedule_sessions = fake_schedule_sessions
    try:
        source = open(path).read()
        namespace = {"__name__": "legacy_config"}
        exec(compile(source, path, "exec"), namespace)
    finally:
        ParallelSched.schedule_sessions = real

    if not captured:
        raise ValueError(f"{path} never called schedule_sessions")

    # Recover the `am = [...]` / `long = [...]` style locals as named slot groups,
    # so the readable shorthand survives the conversion.
    slot_groups = {}
    for key, value in namespace.items():
        if key.startswith("_") or key in ("num_sessions", "num_tracks"):
            continue
        if isinstance(value, list) and value and all(isinstance(v, int) for v in value):
            slot_groups[key] = sorted(set(value))

    captured["name"] = os.path.splitext(os.path.basename(path))[0]
    captured["slot_groups"] = slot_groups
    return normalize(captured)
