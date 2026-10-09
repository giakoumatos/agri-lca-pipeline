"""
Run the Pipeline -- meta model, the three models, checks and review

The whole chain for new datasets, in one run:

  meta model, prepare   start each working copy afresh from input datasets/ and
                        correct it (names, groups, compartments, geography), so
                        the models build on well-mapped exchanges
  OLCApest              pesticide emissions
  Multinutrient         multinutrient fertilisers split per nutrient
  Agri Diesel           diesel of the field operations (and the split Excels)
  meta model, checks    the rows the models added are corrected too, every
                        checklist check runs (both review skills), and the
                        project's review workbook is written:
                        output/<project>/<project> review.xlsx
  verify                is every workbook complete, and its .spold current?

Every save rewrites that dataset's .spold, so the output is always converted.
At the end the review window opens on the datasets just run: the Corrections
tab lists what this run corrected, Needs you what is left for you, and
"Open checklist" opens the review workbook (OPEN_REVIEW_WINDOW below).

To leave a step out, comment out its line in STEPS below.  Without the
"prepare" step the models start from input datasets/ (not from an earlier
run's output), exactly as run_three_models.py does.

USAGE:
    python run_pipeline.py          # show the input chooser, then run
    python run_pipeline.py --no-ui  # skip the chooser, use the config default

Author: Andreas Giakoumatos
Last Updated: 2026-10-05
"""

import os
import sys

# Python's compiled caches in one folder (.venv/pycache), not a __pycache__
# beside every code folder.
sys.pycache_prefix = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.venv', 'pycache')
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'pipeline'))
from run_three_models import BATCH, PROJECT, choose_run, run_steps  # noqa: E402
from config_new_datasets import (OLCAPEST_DIR, MULTINUTRIENT_DIR, AGRI_DIESEL_DIR,  # noqa: E402
                                 META_MODEL_DIR, SPOLD_DIR, PIPELINE_DIR)

META = os.path.join(META_MODEL_DIR, 'meta_model_new.py')
OLCAPEST = os.path.join(OLCAPEST_DIR, 'read_pesticide_data_new.py')
MULTINUTRIENT = os.path.join(MULTINUTRIENT_DIR, 'multinutrient_calculator_new.py')
DIESEL = os.path.join(AGRI_DIESEL_DIR, 'agri_diesel_calculator_new.py')
VERIFY = os.path.join(PIPELINE_DIR, 'verify_output.py')
SPOLD = os.path.join(SPOLD_DIR, 'spold_converter_new.py')
SPLITS = os.path.join(PIPELINE_DIR, 'split_edits.py')

# ── The steps, in order.  Comment out a line to skip that step. ─────────────
STEPS = [
    ('Meta model -- prepare',          META,          BATCH, '--fresh', '--no-downstream'),
    ('OLCApest',                       OLCAPEST,      BATCH, '--from-output'),
    ('Multinutrient',                  MULTINUTRIENT, BATCH),
    ('Agri Diesel',                    DIESEL,        BATCH),
    ('Meta model -- checks and review', META,         BATCH, '--no-downstream'),
    # The split Excels and every .spold, once, from the final workbooks (the
    # steps above skip them).  The splits are kept as built, so edits made in
    # them later can be carried into the datasets (split_edits.py, Re-check).
    ('Rebuild split Excels',           SPLITS,        '--project', PROJECT, '--rebuild'),
    ('Write .spold files',             SPOLD,         BATCH),
    ('Verify output',                  VERIFY,        '--project', PROJECT),
]

# These check the results rather than produce them: a failure is reported,
# and never stops the run or costs the models their output.
ADVISORY = ('Meta model -- checks and review', 'Verify output')

# Open the review window when the run ends (not with --no-ui).
OPEN_REVIEW_WINDOW = True


def main() -> int:
    steps = list(STEPS)
    if not any('--fresh' in step for step in steps):
        # Without the prepare step there is no fresh working copy, so OLCApest
        # starts from input datasets/ rather than from an earlier run's output.
        steps = [tuple(a for a in step if a != '--from-output') for step in steps]

    chosen = choose_run('--no-ui' in sys.argv, recheck_option=True)
    if chosen is None:
        print("Chooser closed without running. Exiting.")
        return 0
    project, files, mode = chosen
    started = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if mode == 'recheck':
        # The chooser's Re-check button: the output workbooks as they stand,
        # checked again without recalculating (recheck.py).
        import recheck
        code = recheck.run(project, files)
    else:
        code = run_steps(steps, project, files, advisory=ADVISORY)
        # What the datasets were calculated from -- so recheck.py can tell
        # when a hand edit needs the calculators again.  Only after a run
        # that included all three.
        if code == 0 and {OLCAPEST, MULTINUTRIENT, DIESEL} <= {step[1] for step in steps}:
            from config_new_datasets import save_fingerprints
            save_fingerprints(project, files)

    review = os.path.join(HERE, 'output', project, f'{project} review.xlsx')
    if os.path.isfile(review):
        print(f"\n  Review workbook: {review}")
        print("  Answer in its Action_list (one answer covers every dataset on a row),")
        print("  then run this again -- or the meta model -- to apply the answers.")

    if OPEN_REVIEW_WINDOW and '--no-ui' not in sys.argv:
        print()
        print("  Opening the review window...")
        sys.stdout.flush()
        sys.path.insert(0, META_MODEL_DIR)
        from meta_model_ui import show_ui
        show_ui(project=project, filenames=files, history_since=started)
    return code


if __name__ == '__main__':
    sys.exit(main())
