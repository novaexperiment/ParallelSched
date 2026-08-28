"""Convert the original .py meeting scripts into configs/*.yaml.

Usage:  python convert_legacy_configs.py [script.py ...]

With no arguments, converts every *YEAR*.py script in this directory. Each
conversion is verified by solving both the original script and the generated
YAML and checking the agendas match, so a silent mistranslation cannot slip
through. Existing YAML files are left alone unless --force is given.
"""

import glob
import os
import re
import sys

import ParallelSched
import schema

HERE = os.path.dirname(os.path.abspath(__file__))
LEGACY = re.compile(r"(19|20)\d{2}")


def legacy_scripts():
    found = []
    for path in sorted(glob.glob(os.path.join(HERE, "*.py"))):
        name = os.path.basename(path)
        if name in ("ParallelSched.py", "schema.py", "app.py",
                    os.path.basename(__file__)):
            continue
        if LEGACY.search(name):
            found.append(path)
    return found


def agenda_of(kwargs):
    result = ParallelSched.solve(**kwargs)
    return result.solution, result


def convert(path, force=False):
    name = os.path.splitext(os.path.basename(path))[0]
    target = os.path.join(schema.CONFIG_DIR, f"{name}.yaml")

    cfg = schema.from_legacy_script(path)

    # Solve straight from the captured Python values...
    original_kwargs = schema.to_solver_kwargs(cfg)
    original_agenda, _ = agenda_of(original_kwargs)

    if os.path.exists(target) and not force:
        print(f"  {name}: configs/{name}.yaml already exists, skipping (use --force)")
        return True

    schema.save(cfg, target)

    # ...then from the YAML actually written to disk, and compare.
    reloaded = schema.load(target)
    reloaded_agenda, _ = agenda_of(schema.to_solver_kwargs(reloaded))

    same = original_agenda == reloaded_agenda
    groups = len(cfg["group_sessions"])
    print(f"  {name}: {groups} groups, {cfg['num_sessions']}x{cfg['num_tracks']} "
          f"-> configs/{name}.yaml  round-trip={'OK' if same else 'MISMATCH'}")
    if not same:
        print(f"    original: {original_agenda}")
        print(f"    reloaded: {reloaded_agenda}")
    return same


def main(argv):
    force = "--force" in argv
    paths = [a for a in argv if not a.startswith("-")] or legacy_scripts()
    print(f"Converting {len(paths)} legacy script(s) into {schema.CONFIG_DIR}")
    ok = all(convert(p, force=force) for p in paths)
    print("All round-trips verified" if ok else "SOME ROUND-TRIPS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
