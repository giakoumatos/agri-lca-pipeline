"""
Run the Three Models

Runs the three new-datasets calculators, in this order, on the datasets you
choose:

  1. OLCApest Calculator (New)
     calculators/olcapest/read_pesticide_data_new.py
  2. Multinutrient Calculator (New)
     calculators/multinutrient/multinutrient_calculator_new.py
  3. Agricultural Diesel Calculator (New)
     calculators/agri_diesel/agri_diesel_calculator_new.py
     (also rebuilds the project's split Excels in crop_datasets_new/)

Each reads the dataset workbook the step before it wrote, in output/<project>/,
and every save also rewrites that dataset's .spold.  OLCApest starts from the
dataset in input datasets/.

This runs the models only.  For the whole chain -- the meta model before and
after them, the checklist checks and the review workbook -- use
run_pipeline.py, which uses the chooser and the step runner defined here.

USAGE:
    python run_three_models.py          # show the input chooser, then run
    python run_three_models.py --no-ui  # skip the chooser, use the config default

Author: Andreas Giakoumatos
Last Updated: 2026-10-05
"""

import os
import sys
import subprocess
import traceback
from datetime import datetime


# ── Console encoding ─────────────────────────────────────────────────────────
# Windows hands a plain console (and any redirected pipe) cp1252, which cannot
# encode the tick and cross this runner prints.  Printing one then raised
# UnicodeEncodeError in the middle of the summary and took the whole run down
# with it -- including the meta review that follows.  Force UTF-8 here, and
# pass it to every step, so no step can die on a character.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
os.environ.setdefault('PYTHONUTF8', '1')


# ── Pipeline definition (from central config) ────────────────────────────────
# The project root -- the folder holding config_new_datasets.py -- on the
# path, wherever in the project this file sits.
_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_ROOT, 'config_new_datasets.py')):
    _ROOT = os.path.dirname(_ROOT)
sys.path.insert(0, _ROOT)
_BASE_DIR = _ROOT
sys.path.insert(0, _BASE_DIR)
from config_new_datasets import (PIPELINE, PIPELINE_PYTHON, SPOLD_CONVERTER_STEP,
                                 DEFER_SPOLD_ENV, DEFER_SPLIT_ENV, SPLIT_STEP_SCRIPT)


# ── Choosing the datasets ─────────────────────────────────────────────────────

def choose_datasets(skip_ui: bool, run_label: str | None = None):
    """(project, [filenames]) to run on, or None -- see choose_run."""
    chosen = choose_run(skip_ui, run_label)
    return chosen[:2] if chosen else None


def choose_run(skip_ui: bool, run_label: str | None = None, recheck_option: bool = False):
    """
    (project, [filenames], mode), or None when the chooser was closed.  mode
    is 'run', or 'recheck' when recheck_option offers the Re-check button and
    it was pressed.

    The input chooser (project_input_ui), or with skip_ui the dataset named
    in the config -- named EXPLICITLY to every step rather than leaving each
    to read the config itself: a step invoked bare is not a pipeline step
    (the meta model, given no arguments, opens its own chooser instead).
    """
    picked = {'run': skip_ui, 'project': '', 'files': None, 'mode': 'run'}
    if not skip_ui:
        from project_input_ui import show_ui

        def on_chosen(project_name, filenames=None, mode='run'):
            picked.update(run=True, project=project_name, files=filenames, mode=mode)

        show_ui(run_callback=on_chosen, run_label=run_label, recheck_option=recheck_option)
        if not picked['run']:
            return None
    project, files = picked['project'], picked['files']
    if not files:
        sys.modules.pop('config_new_datasets', None)
        from config_new_datasets import INPUT_PROJECT_FOLDER, OLCAPEST_INPUT_FILENAME
        project = project or INPUT_PROJECT_FOLDER
        files = [OLCAPEST_INPUT_FILENAME]
        print(f"  [runner] no selection -- using the config default: "
              f"{project} / {OLCAPEST_INPUT_FILENAME}")
    print(f"  [runner] project='{project}'  {len(files)} dataset(s)"
          + ("  -- re-check" if picked['mode'] == 'recheck' else ''))
    return project, list(files), picked['mode']


# ── Running the steps ─────────────────────────────────────────────────────────

# Placeholders a step's arguments may use; the runner fills them in.
BATCH = '<batch>'        # -> --batch '[[project, file], ...]'
PROJECT = '<project>'    # -> the project name


def run_steps(steps, project: str, files: list, advisory=()) -> int:
    """
    Run each (name, script, *args) in order on the chosen datasets, with the
    pipeline Python.  Returns the exit code: 0 when every step that PRODUCES
    results succeeded.

    A failed step stops the run -- except an `advisory` one, which checks the
    results rather than producing them: it is reported, and the run carries
    on, so a check never throws away what the models built.
    """
    import json as _json
    batch = _json.dumps([[project, f] for f in files])
    # A run that ends by converting every workbook needs no .spold from the
    # steps before: each step saves every dataset, and only the last save's
    # .spold would survive.
    env = dict(os.environ)
    # Every step's compiled caches in one folder, as the runner's own.
    env['PYTHONPYCACHEPREFIX'] = os.path.join(_BASE_DIR, '.venv', 'pycache')
    scripts = {os.path.normcase(os.path.abspath(script)) for _name, script, *_ in steps}
    if os.path.normcase(os.path.abspath(SPOLD_CONVERTER_STEP[1])) in scripts:
        env[DEFER_SPOLD_ENV] = '1'
    # ... and one that ends by rebuilding the split Excels needs none from
    # Agri Diesel, whose split the meta model's corrections would outdate.
    if os.path.normcase(os.path.abspath(SPLIT_STEP_SCRIPT)) in scripts:
        env[DEFER_SPLIT_ENV] = '1'
    results = []
    for name, script, *args in steps:
        print()
        print('=' * 80)
        print(f"STEP: {name}  |  {len(files)} dataset(s)  |  "
              f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print('=' * 80)
        command = []
        for arg in args:
            command += (['--batch', batch] if arg == BATCH else
                        [project] if arg == PROJECT else [arg])
        ok = _run(name, script, command, env)
        results.append((name, ok))
        if not ok:
            print()
            if name in advisory:
                print(f"Step '{name}' failed -- carrying on: it checks the results "
                      f"rather than producing them.")
                continue
            print(f"Step '{name}' failed -- stopping.")
            break

    print()
    print('=' * 80)
    print("SUMMARY")
    print('=' * 80)
    for name, ok in results:
        mark = '✓ OK      ' if ok else '⚠ findings' if name in advisory else '✗ FAIL    '
        print(f"    {mark}  {name}")
    skipped = [name for name, *_ in steps if name not in dict(results)]
    for name in skipped:
        print(f"    -           {name}  (not run)")
    failed = [name for name, ok in results if not ok and name not in advisory]
    print()
    if failed or skipped:
        print('Finished with errors.')
        return 1
    if any(not ok for _name, ok in results):
        print('All results were produced; the checks reported findings (see above).')
    else:
        print('All steps completed successfully.')
    return 0


def _run(name: str, script: str, args: list, env: dict | None = None) -> bool:
    """One step as a subprocess of the pipeline Python."""
    if not os.path.isfile(script):
        print(f"  File not found: {script}")
        return False
    if not os.path.isfile(PIPELINE_PYTHON):
        print(f"  PIPELINE_PYTHON not found: {PIPELINE_PYTHON}")
        print("     Update PIPELINE_PYTHON in config_new_datasets.py to point to your venv.")
        return False
    sys.stdout.flush()          # so a step's output follows its heading, in a log too
    try:
        result = subprocess.run([PIPELINE_PYTHON, script, *args], cwd=_BASE_DIR, check=False,
                                env=env)
    except Exception as exc:
        print(f"  {name} failed: {exc}")
        traceback.print_exc()
        return False
    if result.returncode != 0:
        print(f"  {name} exited with code {result.returncode}.")
    return result.returncode == 0


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    chosen = choose_datasets('--no-ui' in sys.argv)
    if chosen is None:
        print("Chooser closed without running. Exiting.")
        sys.exit(0)
    project, files = chosen
    # The three models, as config_new_datasets.PIPELINE lists them.
    steps = [(name, path, BATCH) for name, path in PIPELINE + [SPOLD_CONVERTER_STEP]]
    sys.exit(run_steps(steps, project, files))


if __name__ == "__main__":
    main()
