from ortools.sat.python import cp_model
import random
from tqdm import tqdm  # Import tqdm for progress bar

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

    #print("Debugging change calculation...")  # Debug statement
    # Compare each session in the current agenda to the previous one
    for sess, current_items in current_agenda.items():
        previous_items = previous_agenda.get(sess, [])

        # Normalize the session lists by sorting them and stripping any extra spaces
        normalized_current_items = sorted([item.strip() for item in current_items])
        normalized_previous_items = sorted([item.strip() for item in previous_items])

        #print(f"Session {sess}:")  # Debug statement
        #print(f"  Current: {normalized_current_items}")  # Debug statement
        #print(f"  Previous: {normalized_previous_items}")  # Debug statement

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


def schedule_sessions_once(group_sessions, joint_sessions, strict_non_overlaps, prioritized_non_overlaps, preferences, impossible_slots, num_sessions, num_tracks):
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

    # Limit the number of parallel sessions per time slot (based on num_tracks)
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

        # Limit to num_tracks sessions in parallel
        model.Add(sum(session_count) <= num_tracks)

    # Ensure joint sessions that share groups don't overlap
    for idx1, joint1 in enumerate(joint_sessions):
        for idx2, joint2 in enumerate(joint_sessions):
            if idx1 < idx2 and set(joint1).intersection(set(joint2)):
                # If joint sessions share any group, ensure they are in different sessions
                model.Add(joint_session_vars[idx1] != joint_session_vars[idx2])

    # Minimize penalties for soft constraints
    model.Minimize(sum(penalty * weight for penalty, weight in overlap_penalties))

    # Solve the model
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = random.randint(0, 1000000)  # Random seed for varied results
    status = solver.Solve(model)

    # Check if a feasible or optimal solution is found
    if status == cp_model.FEASIBLE or status == cp_model.OPTIMAL:
        return collect_solution(solver, group_vars, joint_session_vars, joint_sessions, num_sessions)
    else:
        return None


def schedule_sessions(group_sessions, joint_sessions, strict_non_overlaps, prioritized_non_overlaps, preferences, impossible_slots, num_sessions, num_tracks, previous_agenda=None, num_iterations=100):
    """ Wrapper function to run multiple iterations of the scheduler and pick the best solution. """
    best_solution = None
    min_changes = float('inf')

    # Check if previous agenda is given; if not, run a single iteration
    if not previous_agenda:
        num_iterations = 1

    # Run the scheduling function multiple times and collect solutions
    for _ in tqdm(range(num_iterations), desc="Evaluating solutions"):
        solution = schedule_sessions_once(group_sessions, joint_sessions, strict_non_overlaps, prioritized_non_overlaps, preferences, impossible_slots, num_sessions, num_tracks)

        # Skip if no solution was found in this iteration
        if not solution:
            continue

        # Calculate the number of changes relative to the previous agenda
        changes = calculate_changes(solution, previous_agenda)

        # Update the best solution if the current solution has fewer changes
        if changes < min_changes:
            min_changes = changes
            best_solution = solution

    # Print the best solution found with marked changes
    if best_solution:
        print(f"\nBest Solution with {min_changes} changes compared to the previous agenda:")

        for sess, items in sorted(best_solution.items()):
            normalized_current_items = sorted([item.strip() for item in items])
            print(f"\nSession {sess}:")
            if previous_agenda:
                # Get the previous session items and normalize them
                prev_session_items = sorted([item.strip() for item in previous_agenda.get(sess, [])])
                for item in normalized_current_items:
                    # Mark with * if the item is not present in the previous agenda or has moved
                    if item not in prev_session_items:
                        print(f"  {item}*")  # Mark with an asterisk to indicate change
                    else:
                        print(f"  {item}")  # No change
            else:
                for item in normalized_current_items:
                    print(f"  {item}")  # No previous agenda, just print items
    else:
        print("No feasible solution found")
