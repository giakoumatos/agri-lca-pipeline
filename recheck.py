"""
Re-check a project after correcting its output workbooks by hand.

    & .venv\\Scripts\\python.exe recheck.py            # the chooser, as run_pipeline.py
    & .venv\\Scripts\\python.exe recheck.py --no-ui    # the config's project

Runs, on the workbooks in output/<project>/ as you left them:

    Meta model -- checks and review   every check, the corrections, the review
    Write .spold files                each .spold from the corrected workbook
    Verify output                     complete and current

Nothing is recalculated: the calculators start from input datasets/, so a
full run (run_pipeline.py) would replace your edits with the input's values.
That is right as long as the edit leaves alone what the calculators compute
from -- the yield, the inputs bought, the land.  Where it does not, the
datasets are named before the checks run: put the correction in the input
dataset (or answer it in the review's Action_list) and run run_pipeline.py.
"""

import os
import sys

# Python's compiled caches in one folder (.venv/pycache), not a __pycache__
# beside every code folder.
sys.pycache_prefix = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.venv', 'pycache')

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pipeline'))

from run_pipeline import META, SPLITS, SPOLD, VERIFY, ADVISORY
from run_three_models import BATCH, PROJECT, choose_datasets, run_steps

STEPS = [
    ('Meta model -- checks and review', META,  BATCH, '--no-downstream'),
    ('Rebuild split Excels',            SPLITS, '--project', PROJECT, '--rebuild'),
    ('Write .spold files',              SPOLD, BATCH),
    ('Verify output',                   VERIFY, '--project', PROJECT),
]


def main() -> int:
    chosen = choose_datasets('--no-ui' in sys.argv, run_label='Re-check')
    if chosen is None:
        print("Chooser closed without running. Exiting.")
        return 0
    return run(*chosen)


def run(project: str, files: list) -> int:
    """Re-check the chosen datasets -- also run_pipeline.py's Re-check button."""
    # Edits made in the split Excels first: they are corrections of the
    # datasets like any other, checked with them below.
    import split_edits
    split_edits.apply(project)
    from config_new_datasets import stale_calculations
    stale = stale_calculations(project, files)
    if stale:
        print()
        print('  ' + '!' * 74)
        print(f'  {len(stale)} dataset(s) changed in what the calculators compute from (the yield,')
        print('  the inputs bought, the land) since the last full run -- their emissions')
        print('  and diesel are out of date.  Put the correction in the input dataset, or')
        print('  answer it in the review, and run run_pipeline.py:')
        for name in stale:
            print(f'    - {name}')
        print('  ' + '!' * 74)
    code = run_steps(STEPS, project, files, advisory=ADVISORY)
    review = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output', project,
                          f'{project} review.xlsx')
    if os.path.isfile(review):
        print(f"\n  Review workbook: {review}")
    return code


if __name__ == '__main__':
    sys.exit(main())
