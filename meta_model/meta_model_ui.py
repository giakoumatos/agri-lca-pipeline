"""
Meta Model Review UI  —  mechanical corrections are written automatically on
scan; you only decide the judgement calls the model won't make itself.

    python meta_model/meta_model_new.py     # chooser, then this
    python meta_model/meta_model_ui.py      # the same flow

Either entry point chooses the datasets first -- in the SAME chooser
run_pipeline.py uses -- and then runs ONLY this meta model.  It never
runs the three calculators: a picked dataset with no output yet is copied
straight from input datasets/, without pesticide, nutrient or diesel
enrichment.  Run run_pipeline.py (or run_three_models.py) first for those.  show_ui(project,
filenames) opens the window directly on a selection that is already built.

Datasets are normally chosen in project_input_ui -- the pipeline's own input
chooser, reused under this step's name so there is one input UI rather than
two that drift apart -- and handed to show_ui(project, filenames), which opens
already scanned on that selection.

Two tabs, matching the two directions the model talks to you in:

  Corrections   the IMPORTANT corrections the model makes automatically --
                the locked yield line, the standard opening, ISIC, an empty
                comment column filled from this dataset's own facts. These
                are written to disk right after the scan, with no click
                needed: each one has exactly one defensible answer, so it is
                not really a decision. What is left ticked here afterwards
                is a naming call the model flags rather than decides
                ("NEEDS YOUR YES") -- tick and press Save to accept it.
                Below those, in green, is everything written this session
                -- meta cells, exchange renames, group/compartment fixes,
                tidy-ups and your saved answers -- each with its before and
                after.
  Needs you     what the model genuinely WON'T decide for you -- a missing
                yield, a crop name leaking in from another dataset's
                template, a startDate/endDate that disagrees with the prose
                where either side could be the wrong one. Where more than
                one answer is defensible every candidate is offered: press
                "Use proposed" to take one, or type your own answer.

Nothing in "Needs you" (or a leftover "NEEDS YOUR YES" row) is written until
Save is pressed; everything saved is appended to the applied_log sheet of
meta_model_corrections.xlsx.

Answering a "Needs you" row is remembered in meta_model_decisions.json,
across projects: the NEXT dataset that hits the exact same issue -- the same
wrong flow name, the same off-template text -- is fixed on scan without
asking again. Anything whose right answer depends on the rest of the dataset
(a yield, a date, a blank comment) is never reused; see REUSABLE_KINDS.

Author: Andreas Giakoumatos
Last Updated: 2026-09-30
"""

import os
import sys
import time
import threading
import traceback
import tkinter as tk
from tkinter import ttk, messagebox


# The project root -- the folder holding config_new_datasets.py -- on the
# path, wherever in the project this file sits.
_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_ROOT, 'config_new_datasets.py')):
    _ROOT = os.path.dirname(_ROOT)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import meta_model_new as model

_C_BG, _C_ACCENT = '#f5f5f5', '#0078d4'
_TICK, _UNTICK = '☑', '☐'

# Everything, not one project: reviewing corrections is a single job across
# the whole output tree, and every row names the project it came from.
_ALL = '(all projects)'
_ALL_FIELDS = '(all fields)'


class MetaModelUI:
    def __init__(self, root, project=None, filenames=None):
        self.root = root
        self.root.title('Meta Model — review corrections')
        self.root.configure(bg=_C_BG)
        # Never larger than the screen: a fixed 1100 x 660 minimum is more than
        # a small laptop screen shows once the taskbar is counted.
        self.root.minsize(min(1100, self.root.winfo_screenwidth() - 40),
                          min(660, self.root.winfo_screenheight() - 120))

        self._corrections = []          # every proposal from the last scan
        self._checked     = set()       # keys the reviewer wants saved
        self._findings    = []
        self._answers     = {}          # key -> typed answer, for "Needs you"
        self._paths       = []          # dataset files currently selected
        self._busy        = False
        # Set the moment the window starts closing, so a worker thread that
        # finishes afterwards does not try to touch widgets that are gone.
        self._closing     = False
        # Ticks changed or answers typed since the last save.
        self._unsaved     = False
        # Automatic passes chasing corrections a fix just unlocked, capped
        # in _scan_done so this can never loop the UI forever.
        self._auto_pass   = 0
        self._pending_fixes = False
        # Everything written this session, automatic or saved, so the
        # Corrections tab still shows it after the re-scan that follows a
        # write finds nothing left to correct.
        self._applied_log = []

        self._build()
        self._fit_to_screen()
        self._refresh_projects()
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        # Opened from the input chooser: start on exactly what was picked and
        # scan straight away, so the two windows read as one flow.
        if project:
            self._preselect(project, filenames)

    # ── layout ────────────────────────────────────────────────────────────
    def _fit_to_screen(self):
        """
        Cap the window to the screen at open, so Save is never off the
        bottom.  Tk sizes a window with no explicit geometry to the sum of
        what every packed widget asked for, which regularly ran taller than
        a laptop screen -- the window still opened at that height, just
        with its lower part past the bottom of the desktop.
        """
        self.root.update_idletasks()
        width  = min(self.root.winfo_reqwidth(), self.root.winfo_screenwidth() - 20)
        height = self.root.winfo_reqheight()
        available = self.root.winfo_screenheight() - 80
        if height > available or width < self.root.winfo_reqwidth():
            self.root.geometry(f'{width}x{min(height, available)}+0+0')

    def _build(self):
        tk.Label(self.root, text='Meta Model', font=('Segoe UI', 15, 'bold'),
                 bg=_C_BG).pack(pady=(10, 0))
        tk.Label(self.root, text='Choose the datasets and scan. Mechanical corrections are '
                                 'applied automatically -- you only decide the rest.',
                 font=('Segoe UI', 9), bg=_C_BG, fg='#555').pack()

        # ── source ────────────────────────────────────────────────────────
        src = tk.LabelFrame(self.root, text=' 1. Input ', bg=_C_BG,
                            font=('Segoe UI', 9, 'bold'))
        src.pack(fill='x', padx=12, pady=8)

        row = tk.Frame(src, bg=_C_BG)
        row.pack(fill='x', padx=8, pady=6)
        tk.Label(row, text='Project:', bg=_C_BG, font=('Segoe UI', 9)).pack(side='left')
        self._project = tk.StringVar()
        self._project_cb = ttk.Combobox(row, textvariable=self._project, width=42,
                                        state='readonly', font=('Segoe UI', 9))
        self._project_cb.pack(side='left', padx=6)
        self._project_cb.bind('<<ComboboxSelected>>', lambda _e: self._refresh_datasets())
        tk.Button(row, text='Refresh', command=self._refresh_projects,
                  font=('Segoe UI', 8)).pack(side='left')
        self._scan_btn = tk.Button(row, text='Scan selected datasets',
                                   command=self._scan, bg=_C_ACCENT, fg='white',
                                   font=('Segoe UI', 9, 'bold'), padx=12)
        self._scan_btn.pack(side='right')

        lst = tk.Frame(src, bg=_C_BG)
        lst.pack(fill='x', padx=8, pady=(0, 8))
        bar = ttk.Scrollbar(lst, orient='vertical')
        # Fewer rows on a small screen, so the tabs below keep their room.
        rows = 6 if self.root.winfo_screenheight() >= 900 else 3
        self._datasets = tk.Listbox(lst, height=rows, selectmode='extended',
                                    font=('Consolas', 8),
                                    yscrollcommand=bar.set, exportselection=False)
        bar.config(command=self._datasets.yview)
        self._datasets.pack(side='left', fill='both', expand=True)
        bar.pack(side='right', fill='y')

        pick = tk.Frame(src, bg=_C_BG)
        pick.pack(fill='x', padx=8, pady=(0, 6))
        tk.Button(pick, text='All', command=lambda: self._datasets.select_set(0, 'end'),
                  font=('Segoe UI', 8)).pack(side='left')
        tk.Button(pick, text='None', command=lambda: self._datasets.select_clear(0, 'end'),
                  font=('Segoe UI', 8)).pack(side='left', padx=4)
        self._pick_lbl = tk.Label(pick, text='', bg=_C_BG, fg='#555',
                                  font=('Segoe UI', 8))
        self._pick_lbl.pack(side='left', padx=10)

        tk.Label(src, text='Output: ' + os.path.join(model.META_OUTPUT_DIR,
                                                     '<project>') + os.sep,
                 bg=_C_BG, fg='#555', font=('Consolas', 8), anchor='w').pack(
            fill='x', padx=8, pady=(0, 6))

        # ── save ──────────────────────────────────────────────────────────
        # Packed with side='bottom' BEFORE the notebook below, so it claims
        # its space from the bottom of the window first. Packed the other
        # way round, a window taller than the screen (common with the tree
        # and detail panes at their normal size) got capped by Windows and
        # the notebook -- packed first and expanding -- took all of the
        # shrunk cavity, pushing Save off the bottom of the screen entirely.
        bot = tk.Frame(self.root, bg=_C_BG)
        bot.pack(side='bottom', fill='x', padx=12, pady=(0, 10))
        self._save_btn = tk.Button(bot, text='Save ticked corrections',
                                   command=self._save, bg='#107c10', fg='white',
                                   font=('Segoe UI', 10, 'bold'), padx=16, pady=4,
                                   state='disabled')
        self._save_btn.pack(side='right')
        tk.Button(bot, text='Open checklist (review workbook)', command=self._open_review,
                  font=('Segoe UI', 9), padx=10, pady=3).pack(side='right', padx=8)
        self._status = tk.StringVar(value='Ready.')
        tk.Label(bot, textvariable=self._status, bg=_C_BG, fg='#333',
                 font=('Segoe UI', 9), anchor='w').pack(side='left', fill='x', expand=True)

        # ── tabs ──────────────────────────────────────────────────────────
        self._tabs = ttk.Notebook(self.root)
        self._tabs.pack(fill='both', expand=True, padx=12, pady=(0, 6))
        self._build_corrections_tab()
        self._build_needs_tab()

    def _build_corrections_tab(self):
        tab = tk.Frame(self._tabs, bg=_C_BG)
        self._tabs.add(tab, text='  Corrections  ')

        bar = tk.Frame(tab, bg=_C_BG)
        bar.pack(fill='x', pady=4)
        tk.Button(bar, text='Tick all', command=lambda: self._set_all(True),
                  font=('Segoe UI', 8)).pack(side='left')
        tk.Button(bar, text='Untick all', command=lambda: self._set_all(False),
                  font=('Segoe UI', 8)).pack(side='left', padx=4)
        # "Tick all"/"Untick all" only ever touch the rows currently shown,
        # so narrowing to one field first lets you batch-decide just that one.
        tk.Label(bar, text='Field:', bg=_C_BG, font=('Segoe UI', 8)).pack(
            side='left', padx=(10, 2))
        self._corr_filter = tk.StringVar(value=_ALL_FIELDS)
        self._corr_filter_cb = ttk.Combobox(bar, textvariable=self._corr_filter,
                                            width=26, state='readonly',
                                            font=('Segoe UI', 8))
        self._corr_filter_cb.pack(side='left')
        self._corr_filter_cb.bind('<<ComboboxSelected>>',
                                  lambda _e: self._fill_corrections())
        tk.Label(bar, text='Green rows are already written -- click one to see '
                           'before/after.  Others: click to untick, or edit "after".',
                 bg=_C_BG, fg='#555', font=('Segoe UI', 8)).pack(side='left', padx=10)
        self._count_lbl = tk.Label(bar, text='', bg=_C_BG, fg='#333',
                                   font=('Segoe UI', 9, 'bold'))
        self._count_lbl.pack(side='right')

        # The list on top, before/after below, in a split you can drag -- so
        # on a small screen the texts stay visible and the list scrolls.
        split = tk.PanedWindow(tab, orient='vertical', bg=_C_BG, sashwidth=6,
                               sashrelief='raised', showhandle=False)
        split.pack(fill='both', expand=True)
        upper = tk.Frame(split, bg=_C_BG)
        lower = tk.Frame(split, bg=_C_BG)
        split.add(upper, minsize=80, stretch='always')
        split.add(lower, minsize=150, stretch='never')

        columns = ('use', 'project', 'dataset', 'column', 'issues')
        self._tree = ttk.Treeview(upper, columns=columns, show='headings', height=8)
        for name, title, width in (('use', '', 34), ('project', 'Project', 170),
                                   ('dataset', 'Dataset', 280),
                                   ('column', 'Field', 190), ('issues', 'Why', 280)):
            self._tree.heading(name, text=title)
            self._tree.column(name, width=width, anchor='w',
                              stretch=(name == 'issues'))
        bar2 = ttk.Scrollbar(upper, orient='vertical', command=self._tree.yview)
        self._tree.configure(yscrollcommand=bar2.set)
        bar2.pack(side='right', fill='y')
        self._tree.pack(side='left', fill='both', expand=True)
        self._tree.bind('<ButtonRelease-1>', self._toggle_row)
        self._tree.bind('<<TreeviewSelect>>', self._show_detail)

        detail = tk.Frame(lower, bg=_C_BG)
        detail.pack(fill='both', expand=True, pady=(6, 0))
        tk.Label(detail, text='before', bg=_C_BG, fg='#a00',
                 font=('Segoe UI', 8, 'bold')).grid(row=0, column=0, sticky='w')
        after_row = tk.Frame(detail, bg=_C_BG)
        after_row.grid(row=0, column=1, sticky='we')
        tk.Label(after_row, text='after -- editable', bg=_C_BG, fg='#070',
                 font=('Segoe UI', 8, 'bold')).pack(side='left')
        tk.Button(after_row, text='Use edited text', command=self._use_edited_after,
                  bg=_C_ACCENT, fg='white', font=('Segoe UI', 8, 'bold')
                 ).pack(side='right')
        self._before = tk.Text(detail, height=6, width=64, wrap='word',
                               font=('Consolas', 8), bg='#fff4f4')
        self._after = tk.Text(detail, height=6, width=64, wrap='word',
                              font=('Consolas', 8), bg='#f2fbf2')
        self._before.grid(row=1, column=0, sticky='nsew', padx=(0, 4))
        self._after.grid(row=1, column=1, sticky='nsew')
        detail.columnconfigure(0, weight=1)
        detail.columnconfigure(1, weight=1)
        detail.rowconfigure(1, weight=1)          # the texts grow with their pane

    def _build_needs_tab(self):
        tab = tk.Frame(self._tabs, bg=_C_BG)
        self._tabs.add(tab, text='  Needs you  ')

        top = tk.Frame(tab, bg=_C_BG)
        top.pack(fill='x', pady=4)
        tk.Label(top, text='The model will not decide these. Pick a row, then either '
                          'double-click one of the offered answers or type your own '
                          'and press Set.',
                 bg=_C_BG, fg='#555', font=('Segoe UI', 8), anchor='w').pack(side='left')
        tk.Label(top, text='Field:', bg=_C_BG, font=('Segoe UI', 8)).pack(
            side='left', padx=(10, 2))
        self._needs_filter = tk.StringVar(value=_ALL_FIELDS)
        self._needs_filter_cb = ttk.Combobox(top, textvariable=self._needs_filter,
                                             width=26, state='readonly',
                                             font=('Segoe UI', 8))
        self._needs_filter_cb.pack(side='left')
        self._needs_filter_cb.bind('<<ComboboxSelected>>',
                                   lambda _e: self._fill_needs())

        # The list on top, the answer area below, in a split you can drag.
        # Packed one after the other, the list (which expands) took all the
        # height of a small screen and pushed the answer area out of sight;
        # in the split the answer area keeps its minimum height and the list
        # gives up the space instead.
        split = tk.PanedWindow(tab, orient='vertical', bg=_C_BG, sashwidth=6,
                               sashrelief='raised', showhandle=False)
        split.pack(fill='both', expand=True)
        upper = tk.Frame(split, bg=_C_BG)
        lower = tk.Frame(split, bg=_C_BG)
        split.add(upper, minsize=80, stretch='always')
        split.add(lower, minsize=190, stretch='never')

        columns = ('severity', 'project', 'dataset', 'column', 'issue',
                   'offered', 'answer')
        self._needs = ttk.Treeview(upper, columns=columns, show='headings', height=8)
        for name, title, width in (('severity', 'Severity', 90),
                                   ('project', 'Project', 140),
                                   ('dataset', 'Dataset', 230),
                                   ('column', 'Field', 150), ('issue', 'Issue', 210),
                                   ('offered', 'Offered', 70),
                                   ('answer', 'Your answer', 200)):
            self._needs.heading(name, text=title)
            self._needs.column(name, width=width, anchor='w', stretch=(name == 'answer'))
        needs_bar = ttk.Scrollbar(upper, orient='vertical', command=self._needs.yview)
        self._needs.configure(yscrollcommand=needs_bar.set)
        needs_bar.pack(side='right', fill='y')
        self._needs.pack(side='left', fill='both', expand=True)
        self._needs.bind('<<TreeviewSelect>>', self._show_guidance)

        # The answer line first, at the bottom, so it is the last thing a
        # small window takes away; the suggestions fill what is left.
        entry = tk.Frame(lower, bg=_C_BG)
        entry.pack(side='bottom', fill='x', pady=(2, 6))

        self._guidance = tk.Label(lower, text='', bg=_C_BG, fg='#333', anchor='w',
                                  justify='left', wraplength=1040,
                                  font=('Segoe UI', 8))
        self._guidance.pack(side='top', fill='x', pady=(4, 2))
        # Wrap to the window, not to a fixed width a small screen does not have.
        lower.bind('<Configure>', lambda e: self._guidance.configure(
            wraplength=max(200, e.width - 20)))

        # The model's suggestions for the selected row, so the choice is made
        # with the actual text in view rather than from a description of it.
        # Where more than one answer is defensible -- a name missing an axis
        # can be completed several ways -- every candidate is listed and the
        # pick is a click, not a retyped name.
        tk.Label(lower, text='proposed correction(s) -- double-click one to take it',
                 bg=_C_BG, fg='#070', font=('Segoe UI', 8, 'bold'),
                 anchor='w').pack(side='top', fill='x')
        self._options = tk.Listbox(lower, height=4, font=('Consolas', 8),
                                   bg='#f2fbf2', activestyle='none',
                                   exportselection=False)
        self._options.pack(side='top', fill='both', expand=True)
        self._options.bind('<Double-Button-1>', lambda _e: self._use_proposal())
        self._option_values = []
        self._answer = tk.StringVar()
        tk.Entry(entry, textvariable=self._answer, font=('Segoe UI', 9)).pack(
            side='left', fill='x', expand=True)
        tk.Button(entry, text='Use selected', command=self._use_proposal,
                  bg='#107c10', fg='white', font=('Segoe UI', 9, 'bold'),
                  padx=10).pack(side='left', padx=6)
        tk.Button(entry, text='Set', command=self._set_answer, bg=_C_ACCENT, fg='white',
                  font=('Segoe UI', 9, 'bold'), padx=14).pack(side='left')
        tk.Button(entry, text='Clear', command=self._clear_answer,
                  font=('Segoe UI', 9)).pack(side='left', padx=6)

    def _preselect(self, project, filenames=None):
        """Start on the project and files the input chooser handed over."""
        if project in self._project_cb['values']:
            self._project.set(project)
            self._refresh_datasets()
        if filenames:
            wanted = {os.path.splitext(os.path.basename(name))[0].lower()
                      for name in filenames}
            self._datasets.select_clear(0, 'end')
            for index, (_project, path) in enumerate(self._entries):
                stem = os.path.splitext(os.path.basename(path))[0].lower()
                if stem in wanted:
                    self._datasets.select_set(index)
            if not self._datasets.curselection():
                self._datasets.select_set(0, 'end')
                self._status.set('None of the chosen files are in the output '
                                 'folder yet -- showing the whole project.')
        self.root.after(120, self._scan)

    def _open_review(self):
        """Open the project's review workbook -- the checklist, the action list,
        everything the model flagged -- in Excel."""
        import review_workbook
        projects = sorted(self._selected_entries()) or (
            [self._project.get()] if self._project.get() not in ('', _ALL) else [])
        opened = 0
        for project in projects:
            path = review_workbook.review_path(project)
            if os.path.isfile(path):
                os.startfile(path)
                opened += 1
        if not opened:
            messagebox.showinfo(
                'No review workbook yet',
                'Scan the datasets first: the review workbook is written by the scan, to '
                + os.path.join(model.META_OUTPUT_DIR, '<project>', '<project> review.xlsx')
                + '\n(beside the datasets).')

    def show_history(self, project, filenames, since=None):
        """
        Put every correction already made to these datasets into the
        Corrections tab -- from the review workbook's corrections register,
        so they can be reviewed however many times the pipeline has run
        (each run makes them again, and its own scan then finds them done).
        `since` is no longer a filter: the register is shown whole.
        """
        import review_workbook
        try:
            register = review_workbook._register(project)
        except Exception:
            return
        wanted = {os.path.splitext(os.path.basename(n))[0].lower() for n in (filenames or [])}
        for entry in register.values():
            dataset = entry['dataset']
            if wanted and os.path.splitext(dataset)[0].lower() not in wanted:
                continue
            status = review_workbook._status(entry)
            self._applied_log.append({
                'project': project, 'dataset': dataset, 'column': entry['field'],
                'issues': f"{entry['issue']}  [{entry['type']}, {status}; "
                          f"first {entry['first_applied'][:10] or '-'}, "
                          f"last {entry['last_applied'][:10] or '-'}]",
                'before': entry['before'], 'after': entry['after'],
                'when': entry['last_applied'][11:16] or '--:--'})

    # ── data ──────────────────────────────────────────────────────────────
    def _refresh_projects(self):
        projects = [_ALL] + model.list_projects()
        self._project_cb['values'] = projects
        if not self._project.get():
            self._project.set(_ALL)
        self._refresh_datasets()

    def _refresh_datasets(self):
        self._datasets.delete(0, 'end')
        chosen = self._project.get()
        # Each entry keeps its project, so a mixed selection stays unambiguous
        # and two projects can hold a dataset of the same name.
        self._entries = []
        projects = model.list_projects() if chosen in ('', _ALL) else [chosen]
        for project in projects:
            for path in model.dataset_files(project):
                self._entries.append((project, path))
        self._paths = [path for _project, path in self._entries]
        for project, path in self._entries:
            label = (f'{project} / ' if len(projects) > 1 else '') + os.path.basename(path)
            self._datasets.insert('end', label)
        self._datasets.select_set(0, 'end')
        self._pick_lbl.config(
            text=f'{len(self._paths)} dataset(s) across {len(projects)} project(s)')
        self._status.set('Ready. Select datasets and press Scan.')

    def _selected_entries(self):
        """The chosen datasets, grouped by the project they belong to."""
        picked = self._datasets.curselection()
        chosen = [self._entries[i] for i in picked] if picked else list(self._entries)
        by_project: dict = {}
        for project, path in chosen:
            by_project.setdefault(project, []).append(path)
        return by_project

    def _selected_paths(self):
        return [path for paths in self._selected_entries().values() for path in paths]

    def _post(self, call):
        """
        Hand a worker thread's result back to the UI thread.

        Closing the window destroys the root while a scan or a save may still
        be running; the thread then called root.after() on a dead widget and
        died with "main thread is not in main loop", losing the result and
        leaving a stack trace behind.  After the window starts closing there
        is nothing to hand anything back to, so the result is dropped.
        """
        if self._closing:
            return
        try:
            self.root.after(0, call)
        except (tk.TclError, RuntimeError):
            pass

    def _on_close(self):
        """Close the window, but never in the middle of writing files."""
        if self._busy:
            if not messagebox.askyesno(
                    'Still working',
                    'A scan or save is still running.\n\n'
                    'Closing now abandons it -- anything already written to '
                    'disk stays written.\n\nClose anyway?'):
                return
        elif self._unsaved:
            pending = len(self._checked) + len(self._answers)
            if not messagebox.askyesno(
                    'Nothing has been written',
                    f'{pending} correction(s)/answer(s) are ready to save, '
                    f'but Save has not been pressed.\n\n'
                    f'The datasets on disk are unchanged. Close and '
                    f'write nothing?'):
                return
        self._closing = True
        self.root.destroy()

    def _scan(self, _followup=False):
        if self._busy:
            # Silence here read as a dead window: a scan of a whole project
            # takes minutes, and every click in the meantime did nothing
            # without saying so.
            self._status.set('Still working -- wait for the current scan to finish.')
            return
        paths = self._selected_paths()
        if not paths:
            messagebox.showwarning('Nothing selected', 'Select at least one dataset.')
            return
        if not _followup:
            self._auto_pass = 0
        self._busy = True
        self._scan_btn.config(state='disabled')
        self._save_btn.config(state='disabled')
        self._status.set(f'Scanning {len(paths)} dataset(s)...')

        by_project = self._selected_entries()

        def work():
            try:
                corrections, findings = [], []
                for project, project_paths in by_project.items():
                    part_c, part_f = model.scan(project, project_paths)
                    corrections.extend(part_c)
                    findings.extend(part_f)
                self._post(lambda: self._scan_done(corrections, findings))
            except Exception:
                trace = traceback.format_exc()
                self._post(lambda: self._scan_failed(trace))

        threading.Thread(target=work, daemon=True).start()

    def _scan_failed(self, trace):
        self._busy = False
        self._scan_btn.config(state='normal')
        self._save_btn.config(state='normal')
        self._status.set('Scan failed.')
        messagebox.showerror('Scan failed', trace[-1500:])

    def _scan_done(self, corrections, findings):
        self._busy = False
        self._scan_btn.config(state='normal')
        # The model accepts cosmetic tidy-ups outright and keeps them out of
        # its sheet; the UI shows the same list, so drop them here too.
        corrections = [c for c in corrections if not c.get('minor')]
        self._corrections = corrections
        self._findings = findings
        # Mechanical corrections start ticked -- they are applied automatically
        # below.  A suggestion against a BLOCKING finding starts UNticked: those
        # are judgement calls, stay in the Corrections tab, and need a yes from you.
        # A correction you set to apply = no in the review workbook stays
        # rejected here too: never auto-applied, shown as a proposal.
        import review_workbook
        rejected = set()
        for project in {c.get('project', '') for c in corrections}:
            rejected |= review_workbook.rejected_keys(project)
        for c in corrections:
            if c['key'] in rejected:
                c['apply'] = 'no'
        self._checked = {c['key'] for c in corrections
                         if str(c.get('apply', 'yes')).lower() != 'no'}
        self._answers = {}
        self._fill_corrections()
        self._fill_needs()
        self._save_btn.config(state='normal' if (corrections or findings) else 'disabled')
        # Write the project's review workbook as well, so the same review is
        # available outside the UI and the two ways in never disagree.  For
        # the WHOLE project, however few datasets were scanned -- the other
        # datasets are re-checked read-only -- and in the background, since
        # that takes a minute on a large project.
        entries = self._selected_entries()

        def write_reviews():
            for project, project_paths in sorted(entries.items()):
                try:
                    model.write_project_review(
                        project,
                        [f for f in findings if f.project == project],
                        [c for c in corrections if c.get('project') == project],
                        covered=project_paths)
                except Exception:
                    pass

        threading.Thread(target=write_reviews, daemon=True).start()
        # Fixing one deterministic issue can occasionally clear the way for
        # another (e.g. a renamed activity now matches a rule it did not
        # before); a few automatic passes chase that down, capped so a
        # correction that never actually clears cannot loop the UI forever.
        # Exchange fixes (a renamed flow, a misspelled group, a remembered
        # decision) change the exchanges sheet, not a meta cell, so they never
        # appear as a ticked correction -- keyed on the ticks alone, they were
        # never written, and Save then reported "nothing to save".
        self._pending_fixes = any(f.fixed for f in findings)
        if (self._checked or self._pending_fixes) and self._auto_pass < 3:
            self._auto_pass += 1
            self._auto_apply_corrections()
        else:
            self._after_auto_apply(0, 0)

    def _auto_apply_corrections(self):
        """
        Write the mechanical corrections straight away -- ticked means
        deterministic here, not "pick one by one" -- then leave only what
        needs a human decided in the two tabs.
        """
        if not (self._checked or self._pending_fixes):
            self._after_auto_apply(0, 0)
            return
        self._busy = True
        self._scan_btn.config(state='disabled')
        self._save_btn.config(state='disabled')
        self._status.set('Applying automatic corrections...')
        by_project = self._selected_entries()
        checked = set(self._checked)

        def work():
            try:
                committed, applied, written = [], {}, 0
                for project, project_paths in by_project.items():
                    part_c, part_a, part_w = model.commit_selected(
                        project, checked, project_paths, None)
                    committed.extend(part_c)
                    applied.update(part_a)
                    written += part_w
                self._post(lambda: self._after_auto_apply(
                    len(committed), written, committed, applied))
            except Exception:
                trace = traceback.format_exc()
                self._post(lambda: self._scan_failed(trace))

        threading.Thread(target=work, daemon=True).start()

    def _log_applied(self, committed, applied):
        """
        Record what was just written, for the Corrections tab.

        `committed` are meta-cell corrections (with their before/after);
        `applied` are exchange fixes and typed answers, keyed like a finding,
        so the finding from the scan that produced them supplies the before.
        """
        stamp = time.strftime('%H:%M')
        by_finding = {model.finding_key(f): f for f in self._findings}
        by_correction = {model.correction_key(c.get('project', ''), c['dataset'],
                                              c['column']): c for c in self._corrections}
        for c in committed:
            self._applied_log.append({
                'project': c.get('project', ''), 'dataset': c['dataset'],
                'column': c['column'], 'issues': c.get('issues', ''),
                'before': c.get('before', ''), 'after': c.get('after', ''),
                'when': stamp})
        for key, (description, dataset) in applied.items():
            project, _, rest = key.partition('||')
            rest = rest.partition('|')[2]
            finding = by_finding.get(key)
            edited = by_correction.get(key)
            if finding is not None:
                column, kind = finding.column, finding.kind
            elif edited is not None:
                column, kind = edited['column'], ''
            else:
                column, kind = model.split_key_rest(rest)
            if finding is not None:
                before = str(finding.found)
            elif edited is not None:
                before = edited.get('before', '')
            else:
                before = ''
            after = self._answers.get(key) or description
            self._applied_log.append({
                'project': project, 'dataset': dataset, 'column': column,
                'issues': kind or 'your edited text', 'before': before,
                'after': after if after == description else f'{after}\n\n({description})',
                'when': stamp})

    def _after_auto_apply(self, committed, written, committed_rows=(), applied=None):
        self._busy = False
        self._scan_btn.config(state='normal')
        self._unsaved = False
        if committed_rows or applied:
            self._log_applied(committed_rows, applied or {})
        if committed or written:
            self._status.set(
                f'Corrections applied automatically across {written} file(s).  '
                f'Re-scanning for what still needs you...')
            self._scan(_followup=True)
            return
        self._save_btn.config(state='normal' if (self._corrections or self._findings) else 'disabled')
        self._status.set(
            f'Nothing left to fix automatically.  {len(self._needs_rows)} finding(s) need you.'
            if (self._corrections or self._findings) else 'Nothing to save.')
        # Judgement calls only from here: put them in front of you.
        self._tabs.select(1 if self._needs_rows else 0)

    @staticmethod
    def _refresh_field_filter(combo, var, values):
        """
        Keep a field-filter combobox in step with what is actually on screen.

        The selection survives a re-scan when the field is still there;
        it resets to "(all fields)" rather than silently keeping a filter
        that would now hide everything.
        """
        options = [_ALL_FIELDS] + sorted(values)
        combo['values'] = options
        if var.get() not in options:
            var.set(_ALL_FIELDS)

    def _fill_corrections(self):
        self._refresh_field_filter(
            self._corr_filter_cb, self._corr_filter,
            {c['column'] for c in self._corrections}
            | {a['column'] for a in self._applied_log})
        active = self._corr_filter.get()
        self._tree.delete(*self._tree.get_children())
        for index, correction in enumerate(self._corrections):
            if active != _ALL_FIELDS and correction['column'] != active:
                continue
            proposal = str(correction.get('apply', 'yes')).lower() == 'no'
            self._tree.insert('', 'end', iid=str(index),
                              tags=('proposal',) if proposal else (), values=(
                _TICK if correction['key'] in self._checked else _UNTICK,
                correction.get('project', '')[:30],
                correction['dataset'][:52], correction['column'],
                ('NEEDS YOUR YES -- ' if proposal else '')
                + correction['issues'][:70]))
        # What has already been written, newest first -- kept for the whole
        # session, since the re-scan after a write finds nothing to correct.
        for index in range(len(self._applied_log) - 1, -1, -1):
            done = self._applied_log[index]
            if active != _ALL_FIELDS and done['column'] != active:
                continue
            self._tree.insert('', 'end', iid=f'a{index}', tags=('applied',), values=(
                '✔', done['project'][:30], done['dataset'][:52], done['column'],
                f'APPLIED {done["when"]} -- {done["issues"][:60]}'))
        self._tree.tag_configure('proposal', background='#fff6e5')
        self._tree.tag_configure('applied', background='#eef7ee', foreground='#355e35')
        self._update_count()

    @staticmethod
    def _is_applied_row(iid) -> bool:
        return str(iid).startswith('a')

    def _fill_needs(self):
        self._needs_rows = [f for f in self._findings
                            if f.severity in model.NEEDS_HUMAN]
        order = {'BLOCKING': 0, 'ACCEPTABLE_FOR_NOW': 1}
        self._needs_rows.sort(key=lambda f: (order.get(f.severity, 2), f.dataset))
        self._refresh_field_filter(self._needs_filter_cb, self._needs_filter,
                                   {f.column for f in self._needs_rows})
        active = self._needs_filter.get()
        self._needs.delete(*self._needs.get_children())
        for index, finding in enumerate(self._needs_rows):
            if active != _ALL_FIELDS and finding.column != active:
                continue
            key = model.finding_key(finding)
            self._needs.insert('', 'end', iid=str(index), values=(
                finding.severity, finding.project[:26], finding.dataset[:46],
                finding.column, finding.kind[:48],
                # How many answers are on offer, so the rows you can settle
                # with a click stand out from the ones needing a value typed.
                (f'{len(finding.options)} options' if finding.options
                 else '1' if finding.proposal else '-'),
                self._answers.get(key, '')))

    def _update_count(self):
        self._count_lbl.config(
            text=f'{len(self._checked)} of {len(self._corrections)} ticked  ·  '
                 f'{len(self._applied_log)} applied this session')

    # ── interaction ───────────────────────────────────────────────────────
    def _toggle_row(self, event):
        row = self._tree.identify_row(event.y)
        if not row or self._is_applied_row(row):
            return             # already written: nothing to tick
        correction = self._corrections[int(row)]
        key = correction['key']
        if key in self._checked:
            self._checked.discard(key)
        else:
            self._checked.add(key)
        self._tree.set(row, 'use', _TICK if key in self._checked else _UNTICK)
        self._unsaved = True
        self._update_count()

    def _set_all(self, value):
        # Only the rows the field filter currently shows -- so narrowing to
        # one field and pressing this decides just that field, not every one.
        pending = [iid for iid in self._tree.get_children()
                   if not self._is_applied_row(iid)]
        visible = {self._corrections[int(iid)]['key'] for iid in pending}
        if value:
            self._checked |= visible
        else:
            self._checked -= visible
        for row in pending:
            self._tree.set(row, 'use', _TICK if value else _UNTICK)
        self._unsaved = True
        self._update_count()

    def _show_detail(self, _event=None):
        selection = self._tree.selection()
        if not selection:
            return
        if self._is_applied_row(selection[0]):
            correction = self._applied_log[int(selection[0][1:])]
        else:
            correction = self._corrections[int(selection[0])]
        for widget, text in ((self._before, correction['before']),
                             (self._after, correction['after'])):
            widget.delete('1.0', 'end')
            widget.insert('1.0', text)

    def _use_edited_after(self):
        """
        Take whatever is typed in the "after" box as the value to save for
        the selected cell, instead of the model's own proposal.

        Stored the same way a typed "Needs you" answer is: it goes straight
        into the sheet on Save, ahead of the tick/untick machinery, since
        typing your own wording already IS the decision.
        """
        selection = self._tree.selection()
        if not selection:
            messagebox.showinfo('Pick a row', 'Select the row you want to edit first.')
            return
        if self._is_applied_row(selection[0]):
            messagebox.showinfo(
                'Already written',
                'This correction is already in the file. To change it, edit '
                'the dataset, or answer it in "Needs you" if it comes back.')
            return
        correction = self._corrections[int(selection[0])]
        edited = self._after.get('1.0', 'end').strip()
        if not edited:
            messagebox.showinfo('Nothing typed',
                                'Type the text to use in the "after" box first.')
            return
        original = correction.setdefault('_model_after', correction['after'])
        key = model.correction_key(correction.get('project', ''),
                                   correction['dataset'], correction['column'])
        if edited == original:
            self._answers.pop(key, None)
            self._tree.set(selection[0], 'issues', correction['issues'][:70])
        else:
            self._answers[key] = edited
            self._tree.set(selection[0], 'issues', 'EDITED -- your text will be used')
        correction['after'] = edited
        self._checked.add(correction['key'])
        self._tree.set(selection[0], 'use', _TICK)
        self._unsaved = True
        self._status.set('Edited text kept -- press Save to write it.')

    def _show_guidance(self, _event=None):
        selection = self._needs.selection()
        if not selection:
            return
        finding = self._needs_rows[int(selection[0])]
        self._guidance.config(
            text=f'{model.how_to_fix(finding)}\n\nNow: {str(finding.found)[:400]}')
        self._answer.set(self._answers.get(model.finding_key(finding), ''))
        self._show_options(finding)

    def _show_options(self, finding):
        """List every candidate answer for the selected finding."""
        self._options.delete(0, 'end')
        # A finding with one suggestion and one with several are shown the
        # same way, so there is only ever one place to look for the answer.
        if finding.options:
            self._option_values = [value for _label, value in finding.options]
            for index, (label, value) in enumerate(finding.options, 1):
                self._options.insert('end', f'{index}. {value}   [{label}]')
        elif finding.proposal:
            self._option_values = [finding.proposal]
            self._options.insert('end', f'1. {finding.proposal}')
        else:
            self._option_values = []
            self._options.insert(
                'end', '(nothing to suggest -- type the value yourself)')
        if self._option_values:
            self._options.selection_set(0)

    def _use_proposal(self):
        """Take the highlighted suggestion as the answer for the selected row."""
        selection = self._needs.selection()
        if not selection:
            messagebox.showinfo('Pick a row', 'Select the row you are answering first.')
            return
        finding = self._needs_rows[int(selection[0])]
        if not self._option_values:
            messagebox.showinfo(
                'Nothing proposed',
                'The model has no correction to suggest for this one -- '
                'type the value yourself.')
            return
        picked = self._options.curselection()
        index = picked[0] if picked else 0
        value = self._option_values[index]
        if '«' in str(value):
            # A proposal with «...» slots is a template, not an answer: it
            # would be refused on save.  Put it in the box to be completed.
            self._answer.set(value)
            messagebox.showinfo(
                'Complete the proposal',
                'This proposal has parts only you can fill in (marked «...»).\n\n'
                'It is now in the answer box: replace each «...» with the real '
                'value, then press Set answer.')
            return
        self._answers[model.finding_key(finding)] = value
        self._needs.set(selection[0], 'answer', value)
        self._answer.set(value)
        self._unsaved = True

    def _set_answer(self):
        selection = self._needs.selection()
        if not selection:
            messagebox.showinfo('Pick a row', 'Select the row you are answering first.')
            return
        finding = self._needs_rows[int(selection[0])]
        key = model.finding_key(finding)
        value = self._answer.get().strip()
        if value:
            self._answers[key] = value
        else:
            self._answers.pop(key, None)
        self._needs.set(selection[0], 'answer', value)
        self._unsaved = True

    def _clear_answer(self):
        self._answer.set('')
        self._set_answer()

    # ── saving ────────────────────────────────────────────────────────────
    def _remember_answers(self):
        """
        Feed every answer about to be saved into the cross-dataset decision
        cache, so the next dataset with the exact same issue is fixed on
        scan rather than asked again -- one review per distinct issue, not
        one per dataset. Answers that cannot be safely reused (a yield, a
        date, an empty column -- a different right answer each time) are
        skipped by model.remember_decision; see model.REUSABLE_KINDS.
        """
        by_finding = {model.finding_key(f): f for f in self._findings}
        for key, value in self._answers.items():
            finding = by_finding.get(key)
            if finding is not None:
                model.remember_decision(finding.column, finding.kind, finding.found,
                                        value, finding.project, finding.dataset)

    def _save(self):
        if self._busy:
            messagebox.showinfo(
                'Still working',
                'A scan is still running. Wait for it to finish, then save -- '
                'your ticks and typed answers are kept.')
            return
        if not self._checked and not self._answers:
            messagebox.showinfo('Nothing to save',
                                'No corrections are ticked and no answers typed.')
            return
        skipped = len(self._corrections) - len(self._checked)
        if not messagebox.askyesno(
                'Save to the dataset files?',
                f'Save {len(self._checked)} correction(s)'
                + (f' and {len(self._answers)} typed answer(s)' if self._answers else '')
                + (f',\nskipping {skipped} you unticked' if skipped else '')
                + f',\n\ninto {len(self._selected_paths())} dataset(s) of "{self._project.get()}"?\n\n'
                  'Every saved change is recorded in the applied_log sheet.'):
            return

        self._remember_answers()

        self._busy = True
        self._save_btn.config(state='disabled')
        self._status.set('Saving...')

        by_project = self._selected_entries()

        def work():
            try:
                committed, applied, written = [], {}, 0
                for project, project_paths in by_project.items():
                    part_c, part_a, part_w = model.commit_selected(
                        project, self._checked, project_paths, self._answers or None)
                    committed.extend(part_c)
                    applied.update(part_a)
                    written += part_w
                self._post(lambda: self._save_done(committed, applied, written))
            except Exception:
                trace = traceback.format_exc()
                self._post(lambda: self._scan_failed(trace))

        threading.Thread(target=work, daemon=True).start()

    def _save_done(self, committed, applied, written):
        self._busy = False
        self._unsaved = False
        self._save_btn.config(state='normal')
        self._log_applied(committed, applied)
        self._status.set(f'Saved {len(committed)} correction(s) and {len(applied)} '
                         f'answer(s) across {written} file(s). Re-scan to confirm.')
        projects = sorted(self._selected_entries())
        import review_workbook
        where = '\n'.join(
            '    ' + review_workbook.review_path(project) for project in projects)
        messagebox.showinfo(
            'Saved',
            f'{len(committed)} correction(s) written\n'
            f'{len(applied)} typed answer(s) applied\n'
            f'{written} dataset file(s) updated\n\n'
            f'Logged in the Changes Applied sheet of:\n{where}')
        self._scan()


def show_ui(project=None, filenames=None, history_since=None):
    """The review window, opened on `project`/`filenames` and scanned.

    history_since ('YYYY-MM-DD HH:MM:SS'): also list in the Corrections tab
    what was written to these datasets since then -- how run_pipeline.py
    shows the corrections it has just made."""
    root = tk.Tk()
    window = MetaModelUI(root, project=project, filenames=filenames)
    if project:
        # Every correction made so far to these datasets, not just this run's.
        window.show_history(project, filenames)
    # In front, for the same reason the chooser does it.
    try:
        root.lift()
        root.attributes('-topmost', True)
        root.after(400, lambda: root.attributes('-topmost', False))
        root.focus_force()
    except Exception:
        pass
    root.mainloop()


if __name__ == '__main__':
    # Running this file directly used to open the review window bare: no
    # dataset chooser, and nothing put through the three models first, so a
    # freshly imported dataset was reviewed with none of the calculators'
    # work in it.  Hand straight over to the meta model's own entry point,
    # which chooses the datasets, runs any that the models have not seen,
    # and then opens this window on them.
    sys.exit(model.main())
