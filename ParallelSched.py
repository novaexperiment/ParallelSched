"""Scheduling engine for NOvA parallel sessions.

Layered so that both the command-line config scripts and the web UI share one model:

    validate_config(...)  -> (errors, warnings)     cheap checks, no solver
    solve(...)            -> SolveResult            builds + solves, never prints
    format_agenda(...)    -> str                    presentation only
    schedule_sessions(...)                          thin wrapper: solve then print

`schedule_sessions` keeps its original signature and output so the existing
per-meeting scripts (summer2026.py and friends) run unchanged.
"""

import difflib

from ortools.sat.python import cp_model

# Single-worker solving is deterministic *and* costs nothing measurable on
# problems this size, so the same config always yields the same agenda. Vary
# random_seed to get a different (equally optimal) arrangement on purpose.
DEFAULT_NUM_WORKERS = 1
DEFAULT_RANDOM_SEED = 0
DEFAULT_MAX_SECONDS = 60.0


class SolveResult:
    """Everything a caller needs to render a solve, with no printing done for it."""

    def __init__(self, solution=None, status="UNKNOWN", num_changes=None,
                 moved=None, warnings=None, errors=None, conflicts=None,
                 objective=None, wall_time=0.0):
        self.solution = solution          # dict[int, list[str]] or None
        self.status = status              # CP-SAT status name, or PRE_FLIGHT_FAILED
        self.num_changes = num_changes    # int, or None when no previous agenda
        self.moved = moved or {}          # dict[int, list[str]] items new to that slot
        self.warnings = warnings or []    # non-blocking, e.g. unknown group names
        self.errors = errors or []        # blocking pre-flight problems
        self.conflicts = conflicts or []  # named constraints that cannot all hold
        self.objective = objective
        self.wall_time = wall_time

    @property
    def ok(self):
        return self.solution is not None


def collect_solution(solver, group_vars, joint_session_vars, joint_sessions, num_sessions):
    """ Helper function to format and return a given solution as an agenda dictionary. """
    session_agenda = {sess: [] for sess in range(1, num_sessions + 1)}

    # Collect group assignments by session
    for group, sessions in group_vars.items():
        for i, session in enumerate(sessions):
            sess = solver.Value(session) + 1  # Convert to 1-based session number
            session_agenda[sess].append(f'{group}')

    # Collect joint session assignments by session
    for idx, joint in enumerate(joint_sessions):
        joint_session = solver.Value(joint_session_vars[idx]) + 1  # Convert to 1-based session number
        joint_groups = " + ".join(joint)
        session_agenda[joint_session].append(f'Joint {joint_groups}')

    return session_agenda


def calculate_changes(current_agenda, previous_agenda):
    """ Calculate the number of changes between the current agenda and the previous agenda. """
    changes = 0
    if not previous_agenda:
        return changes

    # Compare each session in the current agenda to the previous one
    for sess, current_items in current_agenda.items():
        previous_items = previous_agenda.get(sess, [])

        # Normalize the session lists by sorting them and stripping any extra spaces
        normalized_current_items = sorted([item.strip() for item in current_items])
        normalized_previous_items = sorted([item.strip() for item in previous_items])

        # Calculate the number of items that are different or in different sessions
        for item in normalized_current_items:
            if item not in normalized_previous_items:
                changes += 1

    return changes


def compute_moved(solution, previous_agenda):
    """Per slot, which items are new relative to the previous agenda.

    This is the structured form of the ``*`` markers the text output prints, so
    the UI can highlight moves without re-deriving the rule.
    """
    moved = {}
    if not solution:
        return moved
    for sess, items in solution.items():
        current = sorted([item.strip() for item in items])
        if not previous_agenda:
            moved[sess] = []
            continue
        previous = sorted([item.strip() for item in previous_agenda.get(sess, [])])
        moved[sess] = [item for item in current if item not in previous]
    return moved


def convert_values_to_0_based(input_dict):
    """
    Convert all integer values or list of integer values in a dictionary to 0-based indexing.
    This function leaves keys unchanged and only decrements values where applicable.
    """
    def convert_value(val):
        if isinstance(val, int):
            return val - 1  # Convert single integer value to 0-based
        elif isinstance(val, list):
            return [v - 1 for v in val if isinstance(v, int)]  # Convert each item in the list to 0-based
        else:
            return val  # Leave other types unchanged

    return {key: convert_value(value) for key, value in input_dict.items()}


def joint_label(joint):
    """The canonical agenda label for a joint session, e.g. 'Joint T2K + NuX'."""
    return "Joint " + " + ".join(joint)


def _suggest(name, known):
    """'did you mean' helper for mistyped group names."""
    match = difflib.get_close_matches(name, list(known), n=1, cutoff=0.6)
    return f" Did you mean '{match[0]}'?" if match else ""


def validate_config(group_sessions, joint_sessions, strict_non_overlaps,
                    prioritized_non_overlaps, preferences, impossible_slots,
                    num_sessions, num_tracks, previous_agenda=None):
    """Cheap pre-solve checks. Returns (errors, warnings), both lists of strings.

    Errors make the solve pointless; warnings flag things the model silently
    ignores (chiefly names matching no group, which is how typos hide).
    """
    errors, warnings = [], []
    known = set(group_sessions)

    if num_sessions < 1:
        errors.append("Number of time slots must be at least 1.")
    if num_tracks < 1:
        errors.append("Number of parallel tracks must be at least 1.")

    # Capacity: nothing in the model checks this, and violating it just yields a
    # bare "no feasible solution".
    requested = sum(group_sessions.values()) + len(joint_sessions)
    capacity = num_sessions * num_tracks
    if requested > capacity:
        errors.append(
            f"Too many sessions to fit: {requested} requested "
            f"({sum(group_sessions.values())} group + {len(joint_sessions)} joint) but only "
            f"{capacity} places available ({num_sessions} slots x {num_tracks} tracks). "
            f"Add a slot or a track, or drop {requested - capacity} session(s)."
        )

    for group, count in group_sessions.items():
        if count < 1:
            errors.append(f"'{group}' requests {count} sessions; must be at least 1.")
        elif count > num_sessions:
            errors.append(
                f"'{group}' requests {count} sessions but there are only {num_sessions} "
                f"time slots, and a group cannot appear twice in one slot."
            )

    # Slots a group must occupy, counting the joint sessions it belongs to: a
    # joint session cannot share a slot with a member's own session, nor with
    # another joint session that shares a group, so they all need distinct slots.
    def slots_needed(group):
        own = group_sessions.get(group, 0)
        joint_count = sum(1 for joint in joint_sessions if group in joint)
        return own, joint_count, own + joint_count

    def shortfall(group, available, need, explanation):
        own, joint_count, _ = slots_needed(group)
        breakdown = f"{own} of its own"
        if joint_count:
            breakdown += f" plus {joint_count} joint session(s)"
        return (f"'{group}' needs {need} slot(s) ({breakdown}) but {explanation}. "
                f"Give it at least {need}.")

    # A whitelist narrower than what the group actually needs is infeasible, and
    # this message is far clearer than what the solver would say.
    for group, allowed in (preferences or {}).items():
        if not allowed:
            continue
        _, _, need = slots_needed(group)
        if need:
            if len(set(allowed)) < need:
                errors.append(shortfall(
                    group, allowed, need,
                    f"is restricted to {len(set(allowed))} slot(s) {sorted(set(allowed))}"))
            out_of_range = sorted(s for s in set(allowed) if not 1 <= s <= num_sessions)
            if out_of_range:
                errors.append(
                    f"'{group}' is restricted to slot(s) {out_of_range}, outside 1..{num_sessions}."
                )

    for group, blocked in (impossible_slots or {}).items():
        if not blocked:
            continue
        _, _, need = slots_needed(group)
        if need:
            free = [s for s in range(1, num_sessions + 1) if s not in set(blocked)]
            if len(free) < need:
                errors.append(shortfall(
                    group, free, need,
                    f"only {len(free)} slot(s) remain after blocking {sorted(set(blocked))}"))

    # Unknown names. The model deliberately skips these, which is handy for
    # carrying stale constraints around, but it also swallows typos silently.
    def check_name(name, where):
        if name not in known:
            warnings.append(
                f"{where} refers to '{name}', which is not a requested group, "
                f"so it is ignored.{_suggest(name, known)}")

    for pair in strict_non_overlaps:
        for name in pair:
            check_name(name, "Strict conflict")
    for group, conflicts in (prioritized_non_overlaps or {}).items():
        check_name(group, "Soft conflict")
        for name in conflicts:
            check_name(name, f"Soft conflict for '{group}'")
    for group in (preferences or {}):
        check_name(group, "Slot restriction")
    for group in (impossible_slots or {}):
        check_name(group, "Blocked slot")
    for joint in joint_sessions:
        for name in joint:
            check_name(name, f"Joint session '{joint_label(joint)}'")

    if previous_agenda:
        valid_labels = known | {joint_label(j) for j in joint_sessions}
        for slot, items in previous_agenda.items():
            if not 1 <= slot <= num_sessions:
                warnings.append(
                    f"Previous agenda has slot {slot}, outside 1..{num_sessions}; ignored.")
            for item in items:
                item = item.strip()
                if item not in valid_labels:
                    warnings.append(
                        f"Previous agenda slot {slot} lists '{item}', which matches no group or "
                        f"joint session, so it does not anchor anything.{_suggest(item, valid_labels)}"
                    )

    return errors, warnings



class AgendaCheck:
    """What a hand-edited agenda gets wrong, worked out without the solver.

    Mirrors _build_model's rules exactly, including where the model deliberately
    skips one: constraints naming a group that was never requested are ignored
    there, so they are ignored here too and reported as warnings instead.
    """

    def __init__(self, violations=None, soft_violations=None, warnings=None,
                 counts=None, num_changes=None, moved=None):
        self.violations = violations or []        # hard rules broken
        self.soft_violations = soft_violations or []  # [(text, weight)], preferences the solver would penalise
        self.warnings = warnings or []            # nothing is broken, but something is being ignored
        self.counts = counts or {}                # slot -> sessions running in parallel
        self.num_changes = num_changes            # vs previous agenda, or None
        self.moved = moved or {}                  # slot -> items new to that slot

    @property
    def ok(self):
        """True when the agenda breaks no hard rule. Soft conflicts do not count."""
        return not self.violations


def check_agenda(agenda, group_sessions, joint_sessions, strict_non_overlaps,
                 prioritized_non_overlaps, preferences, impossible_slots,
                 num_sessions, num_tracks, previous_agenda=None, slot_names=None):
    """Check a hand-built agenda against the same rules solve() enforces.

    `agenda` is {slot: [label, ...]} exactly as SolveResult.solution gives it.
    `slot_names` is optional and only makes the messages read naturally.
    """
    violations, soft, warnings = [], [], []
    known = set(group_sessions)
    joint_labels = {joint_label(j): list(j) for j in joint_sessions}
    valid_labels = known | set(joint_labels)

    def name(slot):
        if slot_names and 1 <= slot <= len(slot_names):
            return slot_names[slot - 1]
        return f"Slot {slot}"

    placed = {}
    for slot in range(1, num_sessions + 1):
        placed[slot] = [str(i).strip() for i in agenda.get(slot, []) if str(i).strip()]
    for slot in sorted(agenda):
        if not 1 <= slot <= num_sessions:
            warnings.append(
                f"Slot {slot} is outside 1..{num_sessions}, so what is in it is ignored.")

    # label -> the slots holding it, repeats included: a group placed twice in
    # one slot has to stay visible here.
    slots_of = {}
    for slot, items in placed.items():
        for item in items:
            slots_of.setdefault(item, []).append(slot)

    def standalone(group):
        """Slots where the group runs a session of its own."""
        return set(slots_of.get(group, [])) if group in group_sessions else set()

    def in_joint(group):
        """Slots where the group is on stage as part of a joint session."""
        found = set()
        for label, members in joint_labels.items():
            if group in members:
                found |= set(slots_of.get(label, []))
        return found

    for label in sorted(slots_of):
        if label not in valid_labels:
            warnings.append(
                f"'{label}' matches no requested group or joint session, so no rule "
                f"applies to it.{_suggest(label, valid_labels)}")

    # How many of each session made it onto the agenda.
    for group, wanted in sorted(group_sessions.items()):
        have = len(slots_of.get(group, []))
        if have < wanted:
            missing = wanted - have
            violations.append(
                f"'{group}' is on the agenda {have} time(s) but asked for {wanted}. "
                f"Place {missing} more.")
        elif have > wanted:
            violations.append(
                f"'{group}' is on the agenda {have} time(s) but asked for {wanted}. "
                f"Remove {have - wanted}.")

    for slot, items in sorted(placed.items()):
        seen = {}
        for item in items:
            seen[item] = seen.get(item, 0) + 1
        for label, count in sorted(seen.items()):
            if count > 1:
                violations.append(
                    f"{name(slot)}: '{label}' appears {count} times in the one slot; "
                    f"it can only run once at a time.")
        if len(items) > num_tracks:
            violations.append(
                f"{name(slot)} has {len(items)} sessions in parallel but there are only "
                f"{num_tracks} tracks.")

    # Strict conflicts. The model pairs a joint session against the *other*
    # group's own sessions but never against another joint, so neither do we.
    for pair in strict_non_overlaps:
        group1, group2 = pair
        clashing = set()
        if group1 in group_sessions and group2 in group_sessions:
            clashing |= standalone(group1) & standalone(group2)
        if group2 in group_sessions:
            clashing |= in_joint(group1) & standalone(group2)
        if group1 in group_sessions:
            clashing |= in_joint(group2) & standalone(group1)
        for slot in sorted(clashing):
            violations.append(
                f"{name(slot)}: '{group1}' and '{group2}' must never share a slot.")

    for group, blocked in (impossible_slots or {}).items():
        blocked = set(blocked)
        if not blocked:
            continue
        for slot in sorted(standalone(group) & blocked):
            violations.append(f"{name(slot)}: '{group}' is blocked from this slot.")
        for label, members in sorted(joint_labels.items()):
            if group in members:
                for slot in sorted(set(slots_of.get(label, [])) & blocked):
                    violations.append(
                        f"{name(slot)}: '{label}' cannot be here, because '{group}' is "
                        f"blocked from this slot.")

    # Slot restrictions reach every session the group takes part in, its own and
    # any joint session it belongs to, exactly as blocked slots do.
    for group, allowed in (preferences or {}).items():
        allowed = set(allowed)
        if not allowed:
            continue
        for slot in sorted(standalone(group) - allowed):
            violations.append(
                f"{name(slot)}: '{group}' is restricted to slot(s) "
                f"{sorted(allowed)} and cannot be here.")
        for label, members in sorted(joint_labels.items()):
            if group in members:
                for slot in sorted(set(slots_of.get(label, [])) - allowed):
                    violations.append(
                        f"{name(slot)}: '{label}' cannot be here, because '{group}' is "
                        f"restricted to slot(s) {sorted(allowed)}.")

    for label, members in sorted(joint_labels.items()):
        at = slots_of.get(label, [])
        if not at:
            violations.append(f"'{label}' is not on the agenda anywhere.")
        elif len(at) > 1:
            violations.append(
                f"'{label}' is on the agenda {len(at)} times; a joint session runs once.")
        for member in members:
            for slot in sorted(set(at) & standalone(member)):
                violations.append(
                    f"{name(slot)}: '{label}' clashes with '{member}'s own session in "
                    f"the same slot.")

    joints = sorted(joint_labels.items())
    for index, (label1, members1) in enumerate(joints):
        for label2, members2 in joints[index + 1:]:
            if set(members1) & set(members2):
                shared = set(slots_of.get(label1, [])) & set(slots_of.get(label2, []))
                for slot in sorted(shared):
                    violations.append(
                        f"{name(slot)}: '{label1}' and '{label2}' share a group, so they "
                        f"cannot run in parallel.")

    # Soft conflicts. Declaring A-vs-B and B-vs-A is common and would otherwise
    # report the same overlap twice, so keep the strongest weight per pair.
    worst = {}
    for group, conflicts in (prioritized_non_overlaps or {}).items():
        if group not in group_sessions:
            continue
        for index, other in enumerate(conflicts):
            if other not in group_sessions or other == group:
                continue
            weight = 10 ** (len(conflicts) - index)
            for slot in sorted(standalone(group) & standalone(other)):
                key = (slot, frozenset((group, other)))
                if weight > worst.get(key, (0, ""))[0]:
                    worst[key] = (weight, f"{name(slot)}: '{group}' and '{other}' are "
                                          f"running at the same time.")
    for weight, text in sorted(worst.values(), key=lambda item: -item[0]):
        soft.append((text, weight))

    counts = {slot: len(items) for slot, items in placed.items()}

    num_changes = calculate_changes(placed, previous_agenda) if previous_agenda else None
    moved = compute_moved(placed, previous_agenda)

    # Same message from two rules is noise, not emphasis.
    def unique(items):
        seen, out = set(), []
        for item in items:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    return AgendaCheck(violations=unique(violations), soft_violations=soft,
                       warnings=unique(warnings), counts=counts,
                       num_changes=num_changes, moved=moved)

def _build_model(group_sessions, joint_sessions, strict_non_overlaps, prioritized_non_overlaps,
                 preferences, impossible_slots, num_sessions, num_tracks, previous_agenda=None,
                 balance_weight=10, similarity_weight=1, relaxable=False):
    """Build the CP-SAT model.

    When ``relaxable`` is true, every user-supplied hard constraint is attached to
    an assumption literal so an infeasible model can be explained in terms of the
    constraints the user actually wrote. The happy path builds without assumptions
    so normal solving is untouched.
    """
    model = cp_model.CpModel()
    assumptions = []

    def relaxable_constraint(description):
        """Return a literal to enforce `description` under, or None if not relaxing."""
        if not relaxable:
            return None
        lit = model.NewBoolVar(f'assume_{len(assumptions)}')
        model.AddAssumption(lit)
        assumptions.append((lit, description))
        return lit

    def add(constraint, lit):
        if lit is not None:
            constraint.OnlyEnforceIf(lit)

    # Convert 1-based preferences and impossible_slots to 0-based internally
    preferences = convert_values_to_0_based(preferences)
    impossible_slots = convert_values_to_0_based(impossible_slots)

    # Create variables: each group gets assigned multiple sessions (1 to 3)
    group_vars = {}
    for group, num_requested_sessions in group_sessions.items():
        group_vars[group] = [model.NewIntVar(0, num_sessions - 1, f'{group}_{i}') for i in range(num_requested_sessions)]

    # Ensure non-overlapping sessions within each group
    for group, sessions in group_vars.items():
        for i in range(len(sessions)):
            for j in range(i + 1, len(sessions)):
                model.Add(sessions[i] != sessions[j])  # No overlapping sessions for the same group

    # Create variables for joint sessions (separate from group sessions)
    joint_session_vars = {}
    for idx, joint in enumerate(joint_sessions):
        joint_session_vars[idx] = model.NewIntVar(0, num_sessions - 1, f'joint_session_{idx}')

    # Ensure joint sessions don't overlap with their member groups' regular sessions
    for idx, joint in enumerate(joint_sessions):
        joint_session = joint_session_vars[idx]
        for group in joint:
            # Only add constraints for groups that have standalone sessions
            if group not in group_vars:
                continue
            for session in group_vars[group]:
                model.Add(session != joint_session)  # No overlap with group sessions

    # Add impossible slots constraints (converted to 0-based internally)
    for group, impossible_sessions in impossible_slots.items():
        lit = None
        if impossible_sessions and (group in group_vars or any(group in j for j in joint_sessions)):
            shown = sorted(s + 1 for s in impossible_sessions)
            lit = relaxable_constraint(f"'{group}' cannot use slot(s) {shown}")

        # Apply impossible sessions to individual group sessions (only if group exists)
        if group in group_vars:
            for session in group_vars[group]:
                for impossible in impossible_sessions:
                    add(model.Add(session != impossible), lit)

        # Apply impossible sessions to joint sessions if the group is part of the joint session
        for idx, joint in enumerate(joint_sessions):
            if group in joint:
                for impossible in impossible_sessions:
                    add(model.Add(joint_session_vars[idx] != impossible), lit)

    # Enforce strict non-overlap constraints, including for joint sessions
    for non_overlap_pair in strict_non_overlaps:
        group1, group2 = non_overlap_pair
        lit = None
        if group1 in group_vars or group2 in group_vars:
            lit = relaxable_constraint(f"'{group1}' and '{group2}' must never share a slot")

        # Enforce non-overlap between regular group sessions (only if both groups exist)
        if group1 in group_vars and group2 in group_vars:
            for session1 in group_vars[group1]:
                for session2 in group_vars[group2]:
                    add(model.Add(session1 != session2), lit)

        # Enforce non-overlap between joint sessions and strict non-overlap pairs
        # Check if group1 or group2 is part of any joint session
        for idx, joint_session in joint_session_vars.items():
            joint = joint_sessions[idx]
            if group1 in joint and group2 in group_vars:
                for session2 in group_vars[group2]:
                    add(model.Add(joint_session != session2), lit)  # Group1 is in a joint session, no overlap with Group2
            if group2 in joint and group1 in group_vars:
                for session1 in group_vars[group1]:
                    add(model.Add(joint_session != session1), lit)  # Group2 is in a joint session, no overlap with Group1

    # Add prioritized non-overlapping constraints with penalties (soft constraints)
    overlap_penalties = []
    for group, conflicts in prioritized_non_overlaps.items():
        # Skip if the main group doesn't exist as a standalone session
        if group not in group_vars:
            continue
        for i, conflict_group in enumerate(conflicts):
            # Skip conflict if the conflict group doesn't exist as a standalone session
            if conflict_group not in group_vars:
                continue
            for session1 in group_vars[group]:
                for session2 in group_vars[conflict_group]:
                    penalty_var = model.NewBoolVar(f'{group}_overlaps_{conflict_group}_{i}')
                    model.Add(session1 == session2).OnlyEnforceIf(penalty_var)
                    model.Add(session1 != session2).OnlyEnforceIf(penalty_var.Not())
                    overlap_penalties.append((penalty_var, 10 ** (len(conflicts) - i)))  # Prioritize earlier conflicts more strongly

    # Apply preferences (converted to 0-based internally). A restriction covers
    # every session the group takes part in - its own and any joint session it
    # belongs to - which is the same reach impossible_slots has. "This group can
    # only meet in these slots" is not a statement about session bookkeeping, so
    # a group being on stage jointly does not exempt it. That also means a group
    # can be restricted while having no standalone sessions of its own.
    for group, preferred_sessions in preferences.items():
        joined = [idx for idx, joint in enumerate(joint_sessions) if group in joint]
        # An empty list is "no restriction", matching how impossible_slots reads
        # an empty list; forbidding every slot instead would be unsolvable.
        if not preferred_sessions or not (group in group_vars or joined):
            continue
        shown = sorted(s + 1 for s in preferred_sessions)
        lit = relaxable_constraint(f"'{group}' is restricted to slot(s) {shown}")
        restricted = list(group_vars.get(group, []))
        restricted += [joint_session_vars[idx] for idx in joined]
        for session in restricted:
            if lit is None:
                model.AddAllowedAssignments([session], [[val] for val in preferred_sessions])
            else:
                # AddAllowedAssignments cannot be enforced conditionally, so forbid
                # the complement instead - equivalent, and relaxable.
                for val in range(num_sessions):
                    if val not in preferred_sessions:
                        model.Add(session != val).OnlyEnforceIf(lit)

    # Ensure joint sessions that share groups don't overlap
    for idx1, joint1 in enumerate(joint_sessions):
        for idx2, joint2 in enumerate(joint_sessions):
            if idx1 < idx2 and set(joint1).intersection(set(joint2)):
                # If joint sessions share any group, ensure they are in different sessions
                model.Add(joint_session_vars[idx1] != joint_session_vars[idx2])

    # Track the number of sessions in each time slot
    session_counts = []
    for sess in range(num_sessions):
        session_count = []
        # Collect all session variables (groups + joint sessions)
        for group, sessions in group_vars.items():
            for session in sessions:
                session_count.append(model.NewBoolVar(f'{group}_session_{sess}'))
                model.Add(session == sess).OnlyEnforceIf(session_count[-1])
                model.Add(session != sess).OnlyEnforceIf(session_count[-1].Not())

        # Joint sessions
        for idx, joint_session in joint_session_vars.items():
            session_count.append(model.NewBoolVar(f'joint_session_{idx}_{sess}'))
            model.Add(joint_session == sess).OnlyEnforceIf(session_count[-1])
            model.Add(joint_session != sess).OnlyEnforceIf(session_count[-1].Not())

        # Sum up sessions in this time slot
        count_var = model.NewIntVar(0, num_tracks, f'count_{sess}')
        model.Add(count_var == sum(session_count))
        session_counts.append(count_var)

        # Limit to num_tracks sessions in parallel
        model.Add(count_var <= num_tracks)

    # Add balance constraints between sessions
    balance_penalties = []
    for i in range(num_sessions):
        for j in range(i + 1, num_sessions):
            # Create penalty variable for difference between session counts
            diff_plus = model.NewIntVar(0, num_tracks, f'diff_plus_{i}_{j}')
            diff_minus = model.NewIntVar(0, num_tracks, f'diff_minus_{i}_{j}')

            # diff_plus - diff_minus = session_counts[i] - session_counts[j]
            model.Add(diff_plus - diff_minus == session_counts[i] - session_counts[j])

            # Add both directions to penalties
            balance_penalties.extend([diff_plus, diff_minus])

    # Create similarity penalties for deviating from previous agenda
    similarity_penalties = []
    if previous_agenda:
        for slot, sessions in previous_agenda.items():
            slot_idx = slot - 1
            for session in sessions:
                if session.startswith("Joint"):
                    # Handle joint sessions
                    groups = [g.strip() for g in session.replace("Joint ", "").split(" + ")]
                    for idx, joint in enumerate(joint_sessions):
                        if set(joint) == set(groups):
                            penalty_var = model.NewBoolVar(f'joint_{idx}_moved_from_{slot}')
                            model.Add(joint_session_vars[idx] != slot_idx).OnlyEnforceIf(penalty_var)
                            model.Add(joint_session_vars[idx] == slot_idx).OnlyEnforceIf(penalty_var.Not())
                            similarity_penalties.append((penalty_var, 100))  # High weight to preserve joint sessions
                else:
                    # Handle regular sessions
                    group = session.strip()
                    if group in group_vars:
                        # Add penalty for each session of this group not being in this slot
                        for var in group_vars[group]:
                            penalty_var = model.NewBoolVar(f'{group}_moved_from_{slot}')
                            model.Add(var != slot_idx).OnlyEnforceIf(penalty_var)
                            model.Add(var == slot_idx).OnlyEnforceIf(penalty_var.Not())
                            similarity_penalties.append((penalty_var, 50))  # Medium weight to preserve regular sessions

    # Update the objective function to include overlap penalties.
    total_objective = sum(penalty * weight for penalty, weight in overlap_penalties)

    # Update the objective function to have *either* balance or similarity penalties
    if previous_agenda:
        total_objective += similarity_weight * sum(penalty * weight for penalty, weight in similarity_penalties)
    total_objective += balance_weight * sum(balance_penalties)

    model.Minimize(total_objective)

    # Add hints from previous agenda if available
    if previous_agenda:
        # Track which variables have been hinted
        hinted_vars = set()
        assigned_sessions = {group: set() for group in group_vars.keys()}
        assigned_joint_sessions = {idx: None for idx in joint_session_vars.keys()}

        # First pass: collect positive assignments from previous agenda
        for slot, sessions in previous_agenda.items():
            slot_idx = slot - 1  # Convert to 0-based indexing
            for session in sessions:
                if session.startswith("Joint"):
                    # Handle joint sessions
                    groups = [g.strip() for g in session.replace("Joint ", "").split(" + ")]
                    for idx, joint in enumerate(joint_sessions):
                        if set(joint) == set(groups) and idx not in hinted_vars:
                            model.AddHint(joint_session_vars[idx], slot_idx)
                            assigned_joint_sessions[idx] = slot_idx
                            hinted_vars.add(idx)
                else:
                    # Handle regular sessions
                    group = session.strip()
                    if group in group_vars:
                        # Find an unassigned variable for this group
                        for var in group_vars[group]:
                            if var not in hinted_vars and (group not in assigned_sessions or len(assigned_sessions[group]) < len(group_vars[group])):
                                model.AddHint(var, slot_idx)
                                assigned_sessions[group].add(slot_idx)
                                hinted_vars.add(var)
                                break

    return model, group_vars, joint_session_vars, assumptions


def _make_solver(num_workers, random_seed, max_seconds):
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = num_workers
    solver.parameters.random_seed = random_seed
    solver.parameters.max_time_in_seconds = max_seconds
    return solver


MAX_SHRINK_SOLVES = 80


def _explain_infeasible(build_kwargs, num_workers, random_seed, max_seconds):
    """Name the smallest set of user constraints that cannot all hold at once.

    CP-SAT's SufficientAssumptionsForInfeasibility returns a *sufficient* subset,
    which in practice is often much larger than necessary. We then shrink it by
    deletion: drop one constraint at a time and keep the drop whenever the model
    stays infeasible without it. Solves at this problem size are ~0.02 s, so the
    extra passes are cheap and the payoff is a list a human can actually act on.
    """
    model, _, _, assumptions = _build_model(relaxable=True, **build_kwargs)
    if not assumptions:
        return []
    by_index = {lit.Index(): (lit, text) for lit, text in assumptions}

    solver = _make_solver(num_workers, random_seed, max_seconds)
    if solver.Solve(model) != cp_model.INFEASIBLE:
        return []

    candidates = [i for i in solver.SufficientAssumptionsForInfeasibility() if i in by_index]
    if not candidates:
        candidates = list(by_index)

    def infeasible_with(indices):
        model.ClearAssumptions()
        model.AddAssumptions([by_index[i][0] for i in indices])
        return solver.Solve(model) == cp_model.INFEASIBLE

    budget = MAX_SHRINK_SOLVES
    essential = list(candidates)
    for index in list(candidates):
        if budget <= 0 or len(essential) <= 1:
            break
        budget -= 1
        trial = [i for i in essential if i != index]
        if trial and infeasible_with(trial):
            essential = trial  # this constraint was not needed to cause the clash

    seen, conflicts = set(), []
    for index in essential:
        text = by_index[index][1]
        if text not in seen:
            seen.add(text)
            conflicts.append(text)
    return conflicts


def solve(group_sessions, joint_sessions, strict_non_overlaps, prioritized_non_overlaps,
          preferences, impossible_slots, num_sessions, num_tracks, previous_agenda=None,
          balance_weight=10, similarity_weight=1, num_workers=DEFAULT_NUM_WORKERS,
          random_seed=DEFAULT_RANDOM_SEED, max_seconds=DEFAULT_MAX_SECONDS,
          explain=True):
    """Solve and return a SolveResult. Never prints.

    Pre-flight errors short-circuit the solve. On infeasibility, and when
    ``explain`` is set, the model is rebuilt with assumption literals so the
    clashing constraints can be named.
    """
    errors, warnings = validate_config(
        group_sessions, joint_sessions, strict_non_overlaps, prioritized_non_overlaps,
        preferences, impossible_slots, num_sessions, num_tracks, previous_agenda)
    if errors:
        return SolveResult(status="PRE_FLIGHT_FAILED", errors=errors, warnings=warnings)

    build_kwargs = dict(
        group_sessions=group_sessions, joint_sessions=joint_sessions,
        strict_non_overlaps=strict_non_overlaps,
        prioritized_non_overlaps=prioritized_non_overlaps,
        preferences=preferences, impossible_slots=impossible_slots,
        num_sessions=num_sessions, num_tracks=num_tracks,
        previous_agenda=previous_agenda, balance_weight=balance_weight,
        similarity_weight=similarity_weight)

    model, group_vars, joint_session_vars, _ = _build_model(**build_kwargs)
    solver = _make_solver(num_workers, random_seed, max_seconds)
    status = solver.Solve(model)

    if status in (cp_model.FEASIBLE, cp_model.OPTIMAL):
        solution = collect_solution(solver, group_vars, joint_session_vars,
                                    joint_sessions, num_sessions)
        return SolveResult(
            solution=solution,
            status=solver.StatusName(status),
            num_changes=calculate_changes(solution, previous_agenda) if previous_agenda else None,
            moved=compute_moved(solution, previous_agenda),
            warnings=warnings,
            objective=solver.ObjectiveValue(),
            wall_time=solver.WallTime())

    conflicts = []
    if explain and status == cp_model.INFEASIBLE:
        conflicts = _explain_infeasible(build_kwargs, num_workers, random_seed, max_seconds)

    return SolveResult(status=solver.StatusName(status), warnings=warnings,
                       conflicts=conflicts, wall_time=solver.WallTime())


def format_agenda(result, previous_agenda=None):
    """Render the human-readable agenda block, marking moved items with '*'."""
    lines = []
    if previous_agenda:
        lines.append(f"\nSolution with {result.num_changes} changes compared to the previous agenda:")

    for sess, items in sorted(result.solution.items()):
        normalized_current_items = sorted([item.strip() for item in items])
        lines.append(f"\nSession {sess}:")
        moved = result.moved.get(sess, [])
        for item in normalized_current_items:
            if previous_agenda and item in moved:
                lines.append(f"  {item}*")  # Mark with an asterisk to indicate change
            else:
                lines.append(f"  {item}")
    return "\n".join(lines)


def format_previous_agenda(solution):
    """Render the copy-paste `previous_agenda = {...}` block."""
    lines = ["\n\n# Copy-paste format for previous_agenda:", "previous_agenda = {"]
    for sess, items in sorted(solution.items()):
        normalized_items = sorted([item.strip() for item in items])
        items_str = ', '.join(f'"{item}"' for item in normalized_items)
        lines.append(f"    {sess}: [{items_str}],")
    lines.append("}")
    return "\n".join(lines)


def schedule_sessions_once(group_sessions, joint_sessions, strict_non_overlaps, prioritized_non_overlaps, preferences, impossible_slots, num_sessions, num_tracks, previous_agenda=None):
    """ A single run of the scheduling logic to produce one solution. """
    return solve(group_sessions, joint_sessions, strict_non_overlaps, prioritized_non_overlaps,
                 preferences, impossible_slots, num_sessions, num_tracks,
                 previous_agenda, explain=False).solution


def schedule_sessions(group_sessions, joint_sessions, strict_non_overlaps, prioritized_non_overlaps, preferences, impossible_slots, num_sessions, num_tracks, previous_agenda=None, **solver_options):
    """ Schedule parallel sessions with constraints and optional previous agenda to minimize changes. """
    print("\nProceeding with solve...")
    result = solve(group_sessions, joint_sessions, strict_non_overlaps, prioritized_non_overlaps,
                   preferences, impossible_slots, num_sessions, num_tracks, previous_agenda,
                   **solver_options)

    for message in result.errors:
        print(f"\nProblem: {message}")
    for message in result.warnings:
        print(f"\nNote: {message}")

    if result.ok:
        print(format_agenda(result, previous_agenda))
        print(format_previous_agenda(result.solution))
        return result.solution

    print("\nNo feasible solution found")
    for message in result.conflicts:
        print(f"  Cannot all hold at once: {message}")
    return None
