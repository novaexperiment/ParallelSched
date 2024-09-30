from ParallelSched import schedule_sessions

# Event information
num_sessions = 6
num_tracks = 4   # number of parallels per session

# Requested Sessions
group_sessions = {'TB': 2, 'Ops': 1, 'Production': 1, 'Computing': 1, 'Reco': 2, 'DetSyst': 1, 'Beam': 1, 'Xsec': 1, '3F': 2, 'NuX': 2, 'ND': 3, 'Exotics': 2}
joint_sessions = [['TB', 'DetSyst'], ['Xsec','Computing'], ['Reco', 'DetSyst', 'Xsec', 'Production', 'Beam', '3F','Computing']]

# Strict and optional conflicts
strict_non_overlaps = [('Computing', 'Production'), ('3F', 'Xsec')]  # Strict conflicts
prioritized_non_overlaps = {
    'TB': ['DetSyst', 'Reco', '3F', 'NuX'],  # Ordered by priority (most important first)
    'NuX': ['Computing', 'DetSyst'],
    '3F': ['Reco'],
    'Exotics': ['Ops']
}

# Preferred and impossible slots
# Session numbers count from 0 here
preferences = {'NuX': [5, 6]}
impossible_slots = {'3F': [1], 'DetSyst':[1,5,6], 'Exotics':[5,6]}

# Previous version of the agenda to try to minimize changes from
previous_agenda = {
    1: [
        "Computing",
        "Reco",
        "DetSyst",
        "Exotics"
    ],
    2: [
        "TB",
        "Ops",
        "Production",
        "Xsec"
    ],
    3: [
        "Beam",
        "3F",
        "Exotics",
        "Joint TB + DetSyst"
    ],
    4: [
        "TB",
        "ND",
        "Joint Reco + DetSyst + Xsec + Production + Beam + 3F + Computing"
    ],
    5: [
        "NuX",
        "3F",
        "ND"
    ],
    6: [
        "NuX",
        "Reco",
        "ND",
        "Joint Xsec + Computing"
    ]
}


# Run the scheduler
schedule_sessions(group_sessions, joint_sessions, 
                  strict_non_overlaps, prioritized_non_overlaps, 
                  preferences, impossible_slots, 
                  num_sessions=num_sessions, num_tracks=num_tracks,
                  previous_agenda=previous_agenda,
                  num_iterations=5000)
