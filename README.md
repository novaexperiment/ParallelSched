# ParallelSched

Tool for scheduling parallel sessions at NOvA collaboration meetings. You describe
which groups want sessions and which of them must not clash, and a constraint
solver (Google OR-Tools CP-SAT) builds an agenda that satisfies the hard rules,
minimises the soft ones, and keeps the parallel tracks evenly filled.

There are two ways to use it. They read and write the same meetings, so you can
switch between them freely.

## 1. The app (no Python knowledge needed)

### Getting it, without git

Send this link — it downloads a ZIP straight away, no GitHub account and no
`git` needed:

<https://github.com/novaexperiment/ParallelSched/archive/refs/heads/main.zip>

Extract the ZIP, then double-click the launcher inside the extracted folder.
`START HERE.txt` in that folder explains the rest in plain language.

**Extract it first.** Windows and macOS both let you double-click a file from
inside a ZIP without unpacking it, which copies out that one file on its own and
leaves the launcher with nothing to run. Both launchers now detect this and say
so, but it is the most common way this goes wrong.

### Running it

**macOS** — double-click **`Run Scheduler.command`**
**Windows** — double-click **`Run Scheduler.bat`**

It opens in your web browser. A terminal window appears behind it; leave it open
while you work and close it to quit.

The first run downloads the solver, about 100 MB, and takes a few minutes. Later
runs start in seconds. Everything is installed into a private folder
(`~/Library/Application Support/ParallelSched` on macOS,
`%LOCALAPPDATA%\ParallelSched` on Windows) — deliberately *outside* this project
folder, so a project kept in Dropbox does not sync 100 MB of solver binaries.

You need Python 3.9 or newer installed. If it is missing, the launcher opens the
download page in the browser and spells out the steps — including ticking
**"Add python.exe to PATH"**, which the Windows installer leaves off by default
and which everything else depends on.

**One-time macOS note:** if you downloaded this project as a zip rather than
cloning it, macOS quarantines the launcher and refuses to run it on a
double-click. Right-click `Run Scheduler.command` → **Open** → **Open** once, and
it will behave normally afterwards.

Meetings are stored as readable YAML files in `configs/`. The app's sidebar opens
and saves them.

## 2. The command line

```
python3 -m venv scheduler_env
source scheduler_env/bin/activate
pip install -r requirements.txt
python summer2026.py
```

Each `<meeting>.py` script holds the setup as plain Python literals and calls
`schedule_sessions`. `summer2026.py` is the best template. The script prints the
agenda, marking with `*` anything that moved relative to the previous agenda,
then prints a `previous_agenda = {...}` block you can paste back in to iterate.

## What you specify

The app is organised as **Layout** (the shape of the meeting), **Requests** (who wants sessions), **Conflicts**, **Slot restrictions**, **Previous agenda**, and **Solve**.

| Input | Meaning |
| --- | --- |
| Time slots / parallel tracks | The grid: how many session times, and how many can run at once |
| Group sessions | How many sessions each group wants. A group never gets two in the same slot |
| Joint sessions | A session shared by several groups, scheduled in addition to their own |
| Never at the same time | Hard rule. Unsatisfiable ones make the schedule impossible |
| Prefer not at the same time | Soft rule, **ordered by priority** — see below |
| Restrict to slots | Hard whitelist: the group may *only* use these slots |
| Block slots | The group may not use these slots |
| Slot groups | Optional. A named set of slots, applied to many groups at once |
| Previous agenda | Optional baseline; the solver then minimises how much moves |

### Slot groups

A slot group is just a name for a set of time slots — `am` for the morning slots,
`long` for the one long afternoon session. They save repetition: define `am` once,
then on the **Slot restrictions** page use **Quick fill from a slot group** to
apply it to five groups in one click instead of ticking the same twenty boxes.

They are entirely optional. Ticking boxes directly does the same job.

In the app you pick the slots from a list, so there is no format to get right. In
the YAML they are plain lists, and a restriction may refer to one by name:

```yaml
slot_groups:
  am: [1, 2, 5, 6]
  long: [7]
allowed_slots:
  Exotics: am        # same as writing [1, 2, 5, 6]
  Validation: [7]    # or just give the slots directly
```

This is the YAML equivalent of the `am = [1, 2, 5, 6]` variables the Python
config scripts use. Quick fill writes the resulting slot numbers into each
group's own restriction, so the two views never disagree.

### Two things that surprise people

**"Restrict to slots" is a hard constraint, not a preference.** In the Python
scripts this is the `preferences` dictionary, which is a misleading name: it is a
whitelist. Restricting a group to fewer slots than it has sessions makes the
schedule *impossible*, not merely suboptimal.

**Soft-conflict order matters enormously.** Within one group's list, the first
entry is weighted ten times the second and a hundred times the third
(`10 ** (len(conflicts) - i)`). Reordering two entries is a 10x change in how
hard the solver works to avoid each clash. The app shows this as an explicit
Priority column; in the scripts it is the list order.

### Constraints naming groups that are not meeting

Rules may refer to groups with no sessions this time (`Computing` in
`summer2026`). Those are silently ignored by the solver, which is useful for
carrying rules between meetings — but it also means a typo is silently ignored.
Both the app and the scripts now report these as warnings, with a suggested
correction.

## When no schedule is possible

Instead of a bare "no feasible solution", you get either a specific pre-flight
message:

> Too many sessions to fit: 21 requested (20 group + 1 joint) but only 14 places
> available (7 slots x 2 tracks). Add a slot or a track, or drop 7 session(s).

or, for genuine constraint clashes, the smallest set of rules that cannot all
hold at once:

> - 'ND' and 'Neutron' must never share a slot
> - 'ND' is restricted to slot(s) [1, 2, 3]
> - 'Neutron' is restricted to slot(s) [1]

Relaxing any single one of those makes the schedule possible again.

## Reproducibility

The solver runs single-threaded with a fixed random seed, so the same setup
always produces the same agenda. This costs nothing measurable at this problem
size (solves take about 20 ms) and it means re-running does not silently reshuffle
your work.

To see a *different* equally-good arrangement on purpose, use **Try another
arrangement** in the app, or pass a different `random_seed` to
`schedule_sessions`.

## Files

| File | Purpose |
| --- | --- |
| `ParallelSched.py` | The engine: `validate_config`, `solve`, `format_agenda`, `schedule_sessions` |
| `app.py` | The web interface |
| `schema.py` | The YAML meeting format, and the bridge to the solver |
| `configs/*.yaml` | Saved meetings |
| `<meeting>.py` | The original command-line scripts, still fully supported |
| `convert_legacy_configs.py` | Turns a `<meeting>.py` script into `configs/<meeting>.yaml`, verifying the agendas match |

## Updating

There is no auto-update. When there is a fix worth having, pull the project again
and re-run the launcher; it reinstalls dependencies only when `requirements.txt`
has changed.

## Licence

Apache 2.0 — see `LICENSE`.
