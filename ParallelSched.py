from ortools.sat.python import cp_model

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


def schedule_sessions_once(group_sessions, joint_sessions, strict_non_overlaps, prioritized_non_overlaps, preferences, impossible_slots, num_sessions, num_tracks, previous_agenda=None):
    """ A single run of the scheduling logic to produce one solution. """
    model = cp_model.CpModel()

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

    # Ensure joint sessions don’t overlap with their member groups’ regular sessions
    for idx, joint in enumerate(joint_sessions):
        joint_session = joint_session_vars[idx]
        for group in joint:
            for session in group_vars[group]:
                model.Add(session != joint_session)  # No overlap with group sessions

    # Add impossible slots constraints (converted to 0-based internally)
    for group, impossible_sessions in impossible_slots.items():
        # Apply impossible sessions to individual group sessions
        for session in group_vars[group]:
            for impossible in impossible_sessions:
                model.Add(session != impossible)

        # Apply impossible sessions to joint sessions if the group is part of the joint session
        for idx, joint in enumerate(joint_sessions):
            if group in joint:
                for impossible in impossible_sessions:
                    model.Add(joint_session_vars[idx] != impossible)

    # Enforce strict non-overlap constraints, including for joint sessions
    for non_overlap_pair in strict_non_overlaps:
        group1, group2 = non_overlap_pair

        # Enforce non-overlap between regular group sessions
        for session1 in group_vars[group1]:
            for session2 in group_vars[group2]:
                model.Add(session1 != session2)

        # Enforce non-overlap between joint sessions and strict non-overlap pairs
        # Check if group1 or group2 is part of any joint session
        for idx, joint_session in joint_session_vars.items():
            joint = joint_sessions[idx]
            if group1 in joint:
                for session2 in group_vars[group2]:
                    model.Add(joint_session != session2)  # Group1 is in a joint session, no overlap with Group2
            if group2 in joint:
                for session1 in group_vars[group1]:
                    model.Add(joint_session != session1)  # Group2 is in a joint session, no overlap with Group1

    # Add prioritized non-overlapping constraints with penalties (soft constraints)
    overlap_penalties = []
    for group, conflicts in prioritized_non_overlaps.items():
        for i, conflict_group in enumerate(conflicts):
            for session1 in group_vars[group]:
                for session2 in group_vars[conflict_group]:
                    penalty_var = model.NewBoolVar(f'{group}_overlaps_{conflict_group}_{i}')
                    model.Add(session1 == session2).OnlyEnforceIf(penalty_var)
                    model.Add(session1 != session2).OnlyEnforceIf(penalty_var.Not())
                    overlap_penalties.append((penalty_var, 10 ** (len(conflicts) - i)))  # Prioritize earlier conflicts more strongly

    # Apply preferences (converted to 0-based internally)
    for group, preferred_sessions in preferences.items():
        for session in group_vars[group]:
            allowed_values = preferred_sessions
            model.AddAllowedAssignments([session], [[val] for val in allowed_values])

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
        similarity_weight = 1  # Adjust this weight to control importance of similarity
        total_objective += similarity_weight * sum(penalty * weight for penalty, weight in similarity_penalties)
    else:
        balance_weight = 1  # Adjust this weight to control importance of balance
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
    
    print("\nProceeding with solve...")
    solver = cp_model.CpSolver()

    status = solver.Solve(model)

    # Check if a feasible or optimal solution is found
    if status == cp_model.FEASIBLE or status == cp_model.OPTIMAL:
        return collect_solution(solver, group_vars, joint_session_vars, joint_sessions, num_sessions)
    else:
        return None


def schedule_sessions(group_sessions, joint_sessions, strict_non_overlaps, prioritized_non_overlaps, preferences, impossible_slots, num_sessions, num_tracks, previous_agenda=None):
    """ Schedule parallel sessions with constraints and optional previous agenda to minimize changes. """
    solution = schedule_sessions_once(group_sessions, joint_sessions, strict_non_overlaps, prioritized_non_overlaps, 
                                   preferences, impossible_slots, num_sessions, num_tracks, previous_agenda)

    if solution:
        if previous_agenda:
            changes = calculate_changes(solution, previous_agenda)
            print(f"\nSolution with {changes} changes compared to the previous agenda:")

        for sess, items in sorted(solution.items()):
            normalized_current_items = sorted([item.strip() for item in items])
            print(f"\nSession {sess}:")
            if previous_agenda:
                prev_session_items = sorted([item.strip() for item in previous_agenda.get(sess, [])])
                for item in normalized_current_items:
                    if item not in prev_session_items:
                        print(f"  {item}*")  # Mark with an asterisk to indicate change
                    else:
                        print(f"  {item}")
            else:
                for item in normalized_current_items:
                    print(f"  {item}")
        return solution
    else:
        print("\nNo feasible solution found")
        return None
