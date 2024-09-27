# ParallelSched
Tool for scheduling parallel sessions.

This tool depends on the `ortools` package. I suggest installing it in a virtual environment:
```
pytohn3 -m venv scheduler_env
source scheduler_env/bin/activate
pip install ortools
```
An example configuration can be found in `fall2024.py`. You specify the following:
- Single sessions as requested by various groups
- Joint sessions among groups
- Strict and preferred conflicts between groups
- Preferred and impossible sessions

The preferences set for individual groups also propagate to joint sessions containing that group. If the schedule is under-constrained, running the program multiple times will produce multiple possible agendas.
