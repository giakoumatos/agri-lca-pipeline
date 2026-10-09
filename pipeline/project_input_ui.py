"""
Project Input Manager  —  simple, reliable version
Browse or drag files, set a project name, click Import.
Author: Andreas Giakoumatos  /  2026-07-29
"""
import os, re, shutil, time, inspect, tkinter as tk
from tkinter import filedialog, messagebox, ttk

# What the "run the meta model too" tick box starts on.  A missing config
# entry means yes: the pipeline includes the metadata check by default.
try:
    from config_new_datasets import RUN_META_IN_PIPELINE as _RUN_META_DEFAULT
except Exception:
    _RUN_META_DEFAULT = True

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND = True
except ImportError:
    _DND = False

# The project root: the folder holding config_new_datasets.py.
_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.isfile(os.path.join(_ROOT, 'config_new_datasets.py')):
    _ROOT = os.path.dirname(_ROOT)
_BASE_DIR       = _ROOT
_INPUT_DATASETS = os.path.join(_BASE_DIR, "input datasets")

_CONFIG_FILE    = os.path.join(_BASE_DIR, "config_new_datasets.py")

_C_BG, _C_ACCENT = "#f5f5f5", "#0078d4"


class ProjectInputUI:
    def __init__(self, root, run_callback=None, meta_option=False,
                 run_label=None, recheck_option=False):
        self.root = root
        self.root.title("New Dataset Input Manager")
        self.root.configure(bg=_C_BG)
        self.root.resizable(False, False)
        self.root.minsize(520, 480)
        self._queued = []
        # A Run activation arriving within a few hundred ms of the window
        # appearing cannot be a human decision.  Something was occasionally
        # firing Run by itself right after the window was raised -- it was
        # never reproducible under tracing, so rather than guess at the cause
        # the early activation is simply ignored.
        self._opened_at = time.monotonic()
        self._run_cb = run_callback
        self._imported = False
        # Whether this run should include the meta model.  Only offered when
        # the caller asks for it: the meta model opens this same chooser, and
        # there the question would be nonsense.
        self._meta_option = meta_option
        # What the run buttons promise.  The meta model reuses this chooser
        # only to PICK datasets, so a button reading "Run Pipeline" there
        # promised a run that never happened.
        self._run_label = run_label
        # A second way to run an existing project: re-check its output
        # workbooks as they stand (recheck.py), without recalculating.
        self._recheck_option = recheck_option
        self._meta_var = tk.IntVar(value=1 if _RUN_META_DEFAULT else 0)
        self._build()
        self._check_ready()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self):
        pad = dict(padx=16, pady=6)

        tk.Label(self.root, text="New Dataset Input Manager",
                 font=("Segoe UI", 14, "bold"), bg=_C_BG).pack(padx=16, pady=(12,2))
        tk.Label(self.root,
                 text="Enter a project name, add files, then click Import.",
                 font=("Segoe UI", 10), bg=_C_BG, fg="#666").pack(padx=16, pady=(0,6))

        # --- project name ---
        nf = tk.LabelFrame(self.root, text=" Project Name ",
                           font=("Segoe UI",10), bg=_C_BG, padx=8, pady=6)
        nf.pack(fill="x", **pad)
        self._proj = tk.StringVar()
        tk.Entry(nf, textvariable=self._proj, font=("Segoe UI",10),
                 width=50, relief="solid", bd=1).pack(fill="x")
        self._dest_lbl = tk.Label(nf, text="", font=("Segoe UI",8),
                                  fg="#888", bg=_C_BG, anchor="w")
        self._dest_lbl.pack(fill="x", pady=(2,0))
        self._proj.trace_add("write", self._upd_dest)
        self._proj.trace_add("write", lambda *_: self._check_ready())

        # --- load existing project ---
        ef = tk.LabelFrame(self.root, text=" Load Existing Project ",
                           font=("Segoe UI",10), bg=_C_BG, padx=8, pady=6)
        ef.pack(fill="x", **pad)

        ef_row = tk.Frame(ef, bg=_C_BG)
        ef_row.pack(fill="x", pady=(0,4))
        self._exist_var = tk.StringVar()
        self._exist_cb  = ttk.Combobox(ef_row, textvariable=self._exist_var,
                                       state="readonly", font=("Segoe UI",10))
        self._exist_cb.pack(side="left", fill="x", expand=True, padx=(0,6))
        self._exist_cb.bind("<<ComboboxSelected>>", lambda _: self._on_project_selected())
        tk.Button(ef_row, text="Refresh", command=self._refresh_existing,
                  bg="#6c757d", fg="white", relief="flat",
                  font=("Segoe UI",9), cursor="hand2", padx=8).pack(side="left")
        self._run_existing_btn = tk.Button(
            ef_row, text=self._run_label or "Run Pipeline",
            command=self._run_existing_pipeline,
            bg="#107c10", fg="white", relief="flat",
            font=("Segoe UI",9,"bold"), cursor="hand2", padx=8,
            state="disabled" if not self._run_cb else "normal")
        self._run_existing_btn.pack(side="left", padx=(4,0))
        if self._recheck_option:
            tk.Button(ef_row, text="Re-check",
                      command=lambda: self._run_existing_pipeline(mode='recheck'),
                      bg=_C_ACCENT, fg="white", relief="flat",
                      font=("Segoe UI",9,"bold"), cursor="hand2", padx=8,
                      state="disabled" if not self._run_cb else "normal"
                      ).pack(side="left", padx=(4,0))

        # file list inside the existing-project box
        ef_files = tk.Frame(ef, bg=_C_BG)
        ef_files.pack(fill="x")
        ef_sb = ttk.Scrollbar(ef_files, orient="vertical")
        self._exist_lb = tk.Listbox(ef_files, height=4, font=("Consolas",9),
                                    yscrollcommand=ef_sb.set, relief="solid", bd=1,
                                    selectmode="extended", activestyle="none",
                                    selectbackground=_C_ACCENT)
        ef_sb.config(command=self._exist_lb.yview)
        self._exist_lb.pack(side="left", fill="x", expand=True)
        ef_sb.pack(side="right", fill="y")
        tk.Label(ef, text="Select one or more files, then click Run Pipeline.",
                 font=("Segoe UI",8), fg="#888", bg=_C_BG).pack(anchor="w", pady=(2,0))

        self._refresh_existing()

        # --- file area ---
        ff = tk.LabelFrame(self.root, text=" Files ",
                           font=("Segoe UI",10), bg=_C_BG, padx=8, pady=6)
        ff.pack(fill="both", expand=True, **pad)

        dnd_txt = "Drag files here  —  or use Browse" if _DND else "Use Browse to add files"
        self._drop = tk.Label(ff, text=dnd_txt, font=("Segoe UI",11),
                              bg="#e8f4fd", relief="groove", bd=2, height=3)
        self._drop.pack(fill="x", pady=(0,6))
        if _DND:
            self._drop.drop_target_register(DND_FILES)
            self._drop.dnd_bind("<<Drop>>", self._on_dnd_drop)

        br = tk.Frame(ff, bg=_C_BG)
        br.pack(fill="x", pady=(0,4))
        tk.Button(br, text="Browse...", command=self._browse,
                  bg=_C_ACCENT, fg="white", relief="flat",
                  font=("Segoe UI",10), cursor="hand2", padx=10).pack(side="left")
        tk.Button(br, text="Remove selected", command=self._remove,
                  bg="#6c757d", fg="white", relief="flat",
                  font=("Segoe UI",10), cursor="hand2", padx=10).pack(side="left", padx=6)
        tk.Button(br, text="Clear", command=self._clear,
                  bg="#dc3545", fg="white", relief="flat",
                  font=("Segoe UI",10), cursor="hand2", padx=10).pack(side="left")

        lf = tk.Frame(ff, bg=_C_BG)
        lf.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(lf)
        self._lb = tk.Listbox(lf, height=7, font=("Consolas",9),
                              yscrollcommand=sb.set, relief="solid", bd=1,
                              selectmode="extended", activestyle="none",
                              selectbackground=_C_ACCENT)
        sb.config(command=self._lb.yview)
        self._lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self._cnt = tk.StringVar(value="No files added.")
        tk.Label(ff, textvariable=self._cnt, font=("Segoe UI",8),
                 fg="#888", bg=_C_BG, anchor="w").pack(fill="x", pady=(3,0))

        # --- what the run includes ---
        # The metadata check is a separate job from the calculators, and not
        # every run wants it.  Unticked, the run is just the three models and
        # the SPOLD converter: no metadata findings, no review window.
        # Packed BEFORE the buttons: the window is fixed-size with a
        # bottom-anchored status bar, and anything added after them is
        # squeezed out of the layout and never appears.
        if self._meta_option:
            opt = tk.Frame(self.root, bg=_C_BG)
            opt.pack(pady=(6, 0))
            tk.Checkbutton(
                opt, variable=self._meta_var, bg=_C_BG, activebackground=_C_BG,
                font=("Segoe UI", 9), anchor="w",
                text="Also run the meta model and open its review"
            ).pack(side="left")

        # --- bottom buttons ---
        bot = tk.Frame(self.root, bg=_C_BG)
        bot.pack(pady=10)
        self._imp_btn = tk.Button(bot, text="Import to Project Folder",
                  command=self._do_import,
                  bg=_C_ACCENT, fg="white", relief="flat",
                  font=("Segoe UI",11,"bold"), cursor="hand2",
                  padx=16, pady=8, state="disabled")
        self._imp_btn.pack(side="left", padx=(0,8))
        self._run_btn = tk.Button(bot,
                                  text=self._run_label or "Import & Run Pipeline",
                                  command=self._import_and_run,
                                  bg="#107c10", fg="white", relief="flat",
                                  font=("Segoe UI",11,"bold"), cursor="hand2",
                                  padx=16, pady=8, state="disabled")
        self._run_btn.pack(side="left")

        # --- status ---
        self._st = tk.StringVar(value="Ready.")
        tk.Label(self.root, textvariable=self._st, font=("Segoe UI",9),
                 fg="#555", bg="#e0e0e0", anchor="w",
                 padx=8, pady=3).pack(fill="x", side="bottom")

    # ── helpers ───────────────────────────────────────────────────────────────
    def _refresh_existing(self):
        """Populate the existing-project combobox from the project folders."""
        folders = []
        if os.path.isdir(_INPUT_DATASETS):
            folders = sorted(
                d for d in os.listdir(_INPUT_DATASETS)
                if os.path.isdir(os.path.join(_INPUT_DATASETS, d))
            )
        self._exist_cb['values'] = folders
        if folders and not self._exist_var.get():
            self._exist_var.set(folders[0])
            self._on_project_selected()

    def _on_project_selected(self):
        """Populate the file listbox with the selected project's datasets."""
        name = self._exist_var.get().strip()
        self._exist_lb.delete(0, "end")
        if not name:
            return
        folder = os.path.join(_INPUT_DATASETS, name)
        if not os.path.isdir(folder):
            return
        files = sorted(f for f in os.listdir(folder)
                       if os.path.isfile(os.path.join(folder, f)))
        for f in files:
            self._exist_lb.insert("end", f)
        if files:
            self._exist_lb.selection_set(0)  # pre-select first file

    def _too_soon(self):
        """True while the window is too freshly opened for a real click."""
        if time.monotonic() - self._opened_at >= 0.5:
            return False
        self._opened_at = time.monotonic()
        return True

    def _fire(self, project, files, mode='run'):
        """
        Hand the choice to the runner.

        The tick box and the button pressed ('run' or 'recheck') are passed
        only to a callback that asks for them, so a caller written before the
        options existed still works unchanged.
        """
        kwargs = {}
        try:
            parameters = inspect.signature(self._run_cb).parameters
            if 'run_meta' in parameters:
                kwargs['run_meta'] = bool(self._meta_var.get())
            if 'mode' in parameters:
                kwargs['mode'] = mode
        except (TypeError, ValueError):
            pass
        self._run_cb(project, files, **kwargs)

    def _run_existing_pipeline(self, mode='run'):
        """Run -- or re-check -- each selected file of the chosen project."""
        if self._too_soon():
            return
        name = self._exist_var.get().strip()
        if not name:
            messagebox.showwarning("No project selected", "Select a project from the list first.")
            return
        selected_indices = self._exist_lb.curselection()
        if not selected_indices:
            messagebox.showwarning("No file selected", "Select at least one file from the list.")
            return
        selected_files = [self._exist_lb.get(i) for i in selected_indices]
        self._imported = True
        self._st.set(f"Launching {'re-check' if mode == 'recheck' else 'pipeline'}: "
                     f"{name} — {len(selected_files)} dataset(s)")
        if self._run_cb:
            self._fire(name, selected_files, mode)
            self.root.destroy()
        else:
            messagebox.showinfo("Ready",
                f"Project: '{name}'\nDatasets: {', '.join(selected_files)}\n"
                "Run the pipeline manually.")

    def _on_dnd_drop(self, event):
        # tkinterdnd2 gives a Tcl list; splitlist handles spaces in paths correctly
        paths = list(self.root.tk.splitlist(event.data))
        self._add(paths)

    def _check_ready(self):
        ready = bool(self._proj.get().strip()) and bool(self._queued)
        state = "normal" if ready else "disabled"
        self._imp_btn.config(state=state)
        if self._run_cb:
            self._run_btn.config(state=state)

    def _on_close(self):
        if not self._imported:
            has_name = bool(self._proj.get().strip())
            has_files = bool(self._queued)
            if not has_name or not has_files:
                missing = []
                if not has_name:  missing.append("project name")
                if not has_files: missing.append("at least one file")
                msg = "Please provide " + " and ".join(missing) + " before closing."
                messagebox.showwarning("Cannot close yet", msg)
                return
        self.root.destroy()

    def _upd_dest(self, *_):
        n = self._proj.get().strip()
        self._dest_lbl.config(
            text=("-> " + os.path.join(_INPUT_DATASETS, n)) if n else "")

    def _browse(self):
        init_dir = _INPUT_DATASETS if os.path.isdir(_INPUT_DATASETS) else _BASE_DIR
        paths = filedialog.askopenfilenames(
            title="Select files",
            initialdir=init_dir,
            filetypes=[("Datasets",  "*.xlsx *.xls *.spold"),
                       ("Excel",     "*.xlsx *.xls"),
                       # The pipeline reads .spold directly (step 1 converts
                       # it to the DataX sheets), so it belongs in the dialog
                       # rather than behind "All files".
                       ("EcoSpold2", "*.spold"),
                       ("CSV",       "*.csv"),
                       ("All",       "*.*")])
        self._add(list(paths))

    def _add(self, paths):
        added = 0
        for p in paths:
            p = str(p).strip()
            if p and p not in self._queued:
                self._queued.append(p)
                self._lb.insert("end", os.path.basename(p))
                added += 1
        if added:
            self._cnt.set(f"{len(self._queued)} file(s) added.")
            self._st.set(f"Added {added} file(s).")
        self._check_ready()

    def _remove(self):
        for i in sorted(self._lb.curselection(), reverse=True):
            self._lb.delete(i)
            del self._queued[i]
        self._cnt.set(f"{len(self._queued)} file(s) added." if self._queued else "No files added.")
        self._check_ready()

    def _clear(self):
        self._queued.clear()
        self._lb.delete(0, "end")
        self._cnt.set("No files added.")
        self._check_ready()

    # ── import ────────────────────────────────────────────────────────────────
    def _do_import(self):
        project = self._proj.get().strip()
        if not project:
            messagebox.showwarning("Missing", "Please enter a project name.")
            return None
        if not self._queued:
            messagebox.showwarning("Missing", "Please add at least one file.")
            return None

        dest = os.path.join(_INPUT_DATASETS, project)
        print(f"[Import] destination folder: {dest!r}")
        print(f"[Import] files queued: {self._queued}")

        try:
            os.makedirs(dest, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Error", f"Cannot create folder:\n{dest}\n\n{e}")
            return None

        ok, skip, fail = [], [], []
        for src in self._queued:
            src      = os.path.normpath(src)
            dst_file = os.path.join(dest, os.path.basename(src))

            if not os.path.isfile(src):
                fail.append(f"File not found:\n  {src}")
                continue

            # If the file is already at the destination, skip the copy
            if os.path.normpath(src) == os.path.normpath(dst_file):
                skip.append(os.path.basename(src))
                print(f"[Import] skipped (source == destination): {src!r}")
                continue
            if os.path.isfile(dst_file):
                skip.append(os.path.basename(src))
                print(f"[Import] skipped (already exists at dest): {dst_file!r}")
                continue

            try:
                shutil.copy2(src, dest)
                print(f"[Import] copied to {dst_file!r}")
                ok.append(os.path.basename(src))
            except OSError as e:
                if e.winerror == 32:        # ERROR_SHARING_VIOLATION
                    fail.append(
                        f"{os.path.basename(src)}: file is open in another application.\n"
                        "  Please close Excel (or the program using the file) and try again."
                    )
                else:
                    fail.append(f"{os.path.basename(src)}: {e}")
                print(f"[Import] ERROR: {e}")
            except Exception as e:
                fail.append(f"{os.path.basename(src)}: {e}")
                print(f"[Import] ERROR: {e}")

        cfg_updated = self._update_cfg(project)

        if not ok and not skip:
            err_detail = "\n".join(fail) if fail else "Unknown error."
            messagebox.showerror(
                "Import failed",
                f"No files were copied to:\n{dest}\n\n{err_detail}"
            )
            return None

        msg_parts = []
        if ok:    msg_parts.append(f"Copied {len(ok)} file(s).")
        if skip:  msg_parts.append(f"Skipped {len(skip)} (already in folder).")
        if fail:  msg_parts.append("Failed:\n" + "\n".join(fail))
        if cfg_updated:
            msg_parts.append(f"Config updated: project = '{project}'")
        messagebox.showinfo("Import complete", "\n".join(msg_parts))

        self._st.set(f"Imported {len(ok)} file(s) to {dest}")
        self._imported = True
        self._refresh_existing()
        self._clear()

        return True, project

    def _import_and_run(self):
        if self._too_soon():
            return
        result = self._do_import()
        if result and result[0] and self._run_cb:
            project = result[1]
            # Pass the imported filenames so the runner processes only those files
            imported_files = [os.path.basename(f) for f in self._queued] if self._queued else []
            self._fire(project, imported_files or None)
            self.root.destroy()

    def _update_cfg(self, name, input_filename=None):
        if not os.path.isfile(_CONFIG_FILE):
            return False
        try:
            txt = open(_CONFIG_FILE, "r", encoding="utf-8").read()
            new = re.sub(r"(INPUT_PROJECT_FOLDER\s*=\s*)['\"][^'\"]*['\"]",
                         rf"\g<1>'{name}'", txt)
            if input_filename:
                new = re.sub(r"(OLCAPEST_INPUT_FILENAME\s*=\s*)['\"][^'\"]*['\"]",
                             rf"\g<1>'{input_filename}'", new)
            if new == txt:
                return False
            open(_CONFIG_FILE, "w", encoding="utf-8").write(new)
            return True
        except Exception:
            return False


def _raise_window(root):
    """
    Put the window in front.  A Tk window can open behind the editor that
    launched it, which looks exactly like nothing having happened.
    """
    try:
        root.lift()
        root.attributes("-topmost", True)
        root.after(400, lambda: root.attributes("-topmost", False))
        # Deliberately NOT focus_force(): stealing focus is the likeliest way
        # a stray activation reaches a button the moment the window appears.
    except Exception:
        pass


def show_ui(run_callback=None, meta_option=False, run_label=None, recheck_option=False):
    root = TkinterDnD.Tk() if _DND else tk.Tk()
    try:
        root.iconbitmap(default="")
    except Exception:
        pass
    ProjectInputUI(root, run_callback=run_callback, meta_option=meta_option,
                   run_label=run_label, recheck_option=recheck_option)
    _raise_window(root)
    root.mainloop()


def main():
    show_ui()


if __name__ == "__main__":
    main()
