from ParallelSched import schedule_sessions

# Event information
num_sessions = 7
num_tracks = 3   # number of parallels per session

# Requested Sessions
group_sessions = {'TB': 1, 'Ops': 1, 'Production': 1, 'Reco': 1, 'DetSyst': 1, 'Beam': 1, '3F': 2, 'NuX': 1, 'ND': 3, 'ND-WT': 1, 'Exotics': 2, 'T2K': 1, 'Neutron': 1, 'Validation': 1, 'Hold-WT':1, 'AI':1}
joint_sessions = [('T2K','NuX')]

# Strict and optional conflicts
strict_non_overlaps = [('Production', 'Computing'), 
                       ('ND','Neutron'), 
                       ('Validation','3F'),
                       ('ND', 'ND-WT'), ('AI','Reco')
                       ]  # Strict conflicts
prioritized_non_overlaps = {
    'Validation': ['Reco', 'DetSyst'], 
    'ND': ['Neutron'],
    'Neutron': ['DetSyst', 'Ops', 'ND'],
    'T2K': ['NuX'],
    'Production': ['T2K','NuX','Neutron'],
    'TB': ['DetSyst', 'NuX'], 
    'NuX': ['Production', 'DetSyst'],
    '3F': ['Reco', 'T2K'],
    'Ops':['Exotics', 'ND']
}

am = [1, 2, 5, 6]
long = [7]

# Preferred and impossible slots
preferences = {'Exotics': am, 'NuX': am, 'Validation':long, 'ND-WT': long, 'Hold-WT': long, 'AI': [6]}  # Preferred time slots for certain sessions
impossible_slots = {} #{'3F': [1], 'DetSyst':[1,5,6], 'Exotics':[5,6], 'Xsec':[3,1,6]}

# Previous version of the agenda to try to minimize changes from
previous_agenda = {
    1: ["ND", "Production", "TB"],
    2: ["3F", "DetSyst", "Ops"],
    3: ["Beam", "Joint T2K + NuX", "Neutron"],
    4: ["ND", "Reco", "T2K"],
    5: ["3F", "Exotics", "NuX"],
    6: ["AI", "Exotics", "ND"],
    7: ["Hold-WT", "ND-WT", "Validation"],
}

# Run the scheduler
schedule_sessions(group_sessions, joint_sessions, 
                  strict_non_overlaps, prioritized_non_overlaps, 
                  preferences, impossible_slots, 
                  num_sessions=num_sessions, num_tracks=num_tracks,
                  previous_agenda=previous_agenda)
