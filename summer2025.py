from ParallelSched import schedule_sessions

# Event information
num_sessions = 6
num_tracks   = 4   # number of parallels per session

# Requested Sessions
group_sessions = {'TB': 2, 'Ops': 1, 
                  'Reco': 1,  'DetSyst': 1, 'Production': 1, 'Computing': 1, 'Beam': 1, 'Code of Conduct':1,
                  'ND': 3, '3F': 2, 'NuX': 2, 'Exotics': 2, 'NOvA-T2K':1, 'Prod6':1}
joint_sessions = [ ['TB', 'DetSyst'] ]

# Strict and optional conflicts
# Strict conflicts
strict_non_overlaps = [('Computing', 'Production'), 
                       ('Code of Conduct','TB'), 
                       ('Prod6', 'Reco'),
                       ('Prod6', 'DetSyst'),
                       ('Prod6', 'Production'),
                       ('Prod6', 'Beam'),
                       ('Prod6', '3F'),
                       ('Prod6', 'Computing'),
                       ('Prod6', 'NOvA-T2K')  ]

# Ordered by priority (most important first)
prioritized_non_overlaps = {
    'TB': ['DetSyst', 'NuX'],  
    'Ops': ['Exotics','ND'],
    'DetSyst': ['TB', 'NuX'],
    'Computing': ['NuX'],
    'NuX': ['NOvA-T2K','Computing'],
    'Exotics': ['Ops'],
    '3F': ['NOvA-T2K','Prod6'],
    'Exotics': ['Ops'],
    'Code of Conduct': ['Prod6','Computing', 'Ops']
}

# Preferred and impossible slots
# Session numbers count from 1 here
preferences =      {'Exotics':[3,5], 'NOvA-T2K':[1,2,5,6], 'DetSyst':[5,6], 'Beam':[3,5], 'NuX':[1,4] }
impossible_slots = {'3F': [3,4], 'Exotics': [1,2], 'Beam':[1,2], 'DetSyst':[3,4], 'Prod6':[3,4] }

# Previous version of the agenda to try to minimize changes from
previous_agenda = {
    1: [
        "3F",
        "Computing",
        "ND",
        "TB"
    ],
    2: [
        "Joint TB + DetSyst",
        "ND",
        "NOvA-T2K",
        "Production"
    ],
    3: [
        "Beam",
        "Code of Conduct",
        "Exotics",
        "NuX"
    ],
    4: [
        "NuX",
        "Ops"
    ],
    5: [
        "Exotics",
        "Prod6",
        "TB"
    ],
    6: [
        "3F",
        "DetSyst",
        "ND",
        "Reco"
    ]
}


# Run the scheduler
schedule_sessions(group_sessions, joint_sessions, 
                  strict_non_overlaps, prioritized_non_overlaps, 
                  preferences, impossible_slots, 
                  num_sessions=num_sessions, num_tracks=num_tracks,
                  previous_agenda=previous_agenda)
