"""TACZ 1.1.4 枪战利品生成器 GUI (tkinter)
依赖: models / tacz_io / tacz_scan / tacz_link / tacz_compat / tacz_ammo / tacz_loot
标准1.1.4目录: 根/data/<ns>/{index,data,tacz_tags,...}
  - 枪:   data/<ns>/index/guns/*.json + data/<ns>/data/guns/*_data.json
  - 配件: data/<ns>/index/attachments/*.json + data/<ns>/data/attachments/*_data.json
  - 弹药: data/<ns>/index/ammo/*.json + data/<ns>/data/ammo/*_data.json
  - 允许配件: data/<ns>/tacz_tags/attachments/allow_attachments/<gun>.json (id数组/#tag)
  - 通用配件tag: data/<ns>/tacz_tags/attachments/*.json 或 tags/attachments/*.json
战利品: 枪=tacz:modern_kinetic_gun{GunId,GunFireMode}(不预填弹);
        附件=tacz:attachment{AttachmentId} 合并单池随机;
        弹药=tacz:ammo{AmmoId} 按策略给count。
优化: 包列表可滚动+搜索+全选/反选；缺类/纯枪/纯配件/纯弹药不崩；可单独出配件/弹药池。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Dict, List

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

from models import ScanConfig
import tacz_scan
import tacz_link
from tacz_compat import CompatOptions
from tacz_loot import write_loot

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger("gui")


class ScrollCheckList(tk.Frame):
    """Canvas 内嵌 Frame 的复选列表，固定高度可滚动；支持搜索/全选/反选/清空。"""

    def __init__(self, master, height=260, on_change=None, **kw):
        super().__init__(master, **kw)
        self.on_change = on_change
        self.vars: Dict[str, tk.BooleanVar] = {}
        self.meta: Dict[str, str] = {}

        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=2, pady=2)
        ttk.Label(top, text="过滤").pack(side=tk.LEFT, padx=2)
        self.search = ttk.Entry(top)
        self.search.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.search.bind("<KeyRelease>", self._apply_filter)

        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, padx=2)
        ttk.Button(bar, text="全选", command=self.select_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="反选", command=self.invert).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="清空选择", command=self.clear_sel).pack(side=tk.LEFT, padx=2)

        container = ttk.Frame(self)
        container.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self.canvas = tk.Canvas(container, height=height, highlightthickness=0)
        self.yscroll = ttk.Scrollbar(container, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.yscroll.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.yscroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _on_wheel(self, event):
        rx = self.canvas.winfo_rootx()
        ry = self.canvas.winfo_rooty()
        rw = self.canvas.winfo_width()
        rh = self.canvas.winfo_height()
        if rx <= event.x_root <= rx + rw and ry <= event.y_root <= ry + rh:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def set_items(self, items: List[tuple]):
        """items: [(key, text), ...]"""
        self.vars.clear()
        self.meta.clear()
        for key, text in items:
            self.vars[key] = tk.BooleanVar(value=True)
            self.meta[key] = text
        self._apply_filter()

    def _rebuild(self, filt: str):
        for w in self.inner.winfo_children():
            w.destroy()
        for key, text in self.meta.items():
            if filt and filt not in text.lower():
                continue
            v = self.vars.get(key)
            if v is None:
                continue
            ttk.Checkbutton(
                self.inner, text=text, variable=v,
                command=lambda: self.on_change() if self.on_change else None
            ).pack(anchor=tk.W, padx=4, pady=1)
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _apply_filter(self, event=None):
        filt = self.search.get().strip().lower()
        self._rebuild(filt)

    def selected(self) -> List[str]:
        return [k for k, v in self.vars.items() if v.get()]

    def select_all(self):
        for v in self.vars.values():
            v.set(True)
        self._apply_filter()

    def invert(self):
        for v in self.vars.values():
            v.set(not v.get())
        self._apply_filter()

    def clear_sel(self):
        for v in self.vars.values():
            v.set(False)
        self._apply_filter()


class SourceManager:
    """管理扫描源：文件夹 / zip / 游戏根下 tacz 子包（1.1.4）"""

    def __init__(self):
        self.paths: List[Path] = []
        self.labels: Dict[str, str] = {}

    def add(self, p: Path, label: str = "") -> bool:
        p = Path(p)
        norm = Path(str(p).rstrip("\\/").lower())
        for existing in self.paths:
            if Path(str(existing).rstrip("\\/").lower()) == norm:
                return False
        self.paths.append(p)
        self.labels[str(p)] = label or p.name
        return True

    def remove(self, path_str: str):
        target = Path(str(path_str).rstrip("\\/").lower())
        for p in list(self.paths):
            if Path(str(p).rstrip("\\/").lower()) == target:
                self.paths.remove(p)
                self.labels.pop(str(p), None)

    def clear(self):
        self.paths.clear()
        self.labels.clear()

    def items(self):
        return [(p, self.labels.get(str(p), p.name)) for p in self.paths]


class App:
    def __init__(self, master: tk.Tk):
        self.master = master
        master.title("TACZ 1.1.4 枪战利品生成器")
        master.geometry("920x720")
        master.resizable(True, True)

        self.src = SourceManager()
        self._packs = None
        self._cfg = ScanConfig(strict=False, heuristic=True, cross_pack=False, auto_fire=True)
        self.pack_vars: Dict[str, tk.BooleanVar] = {}

        self.var_strict = tk.BooleanVar(value=False)
        self.var_heuristic = tk.BooleanVar(value=True)
        self.var_cross = tk.BooleanVar(value=False)
        self.var_auto_fire = tk.BooleanVar(value=True)

        self.var_rolls = tk.IntVar(value=2)
        self.var_roll_min = tk.IntVar(value=1)
        self.var_roll_max = tk.IntVar(value=3)
        self.var_slots = tk.StringVar(value="")
        self.var_extmag_auto = tk.BooleanVar(value=True)
        self.var_extmag_bonus = tk.DoubleVar(value=2.0)

        self.var_ammo = tk.StringVar(value="pack_default")

        self.var_out = tk.StringVar(value=str(Path.home() / "tacz_loot_output"))
        self.var_out_ns = tk.StringVar(value="tacz_loot")
        self.var_merge = tk.BooleanVar(value=False)
        self.var_per_ns = tk.BooleanVar(value=True)
        self.var_gun_chance = tk.DoubleVar(value=1.0)

        self.var_out_att = tk.BooleanVar(value=False)
        self.var_out_ammo = tk.BooleanVar(value=False)

        self.pack_list = None
        self._build()
        self._install_log_handler()

    # ---------------- UI ----------------
    def _build(self):
        nb = ttk.Notebook(self.master)
        nb.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        self._tab_sources(nb)
        self._tab_scan(nb)
        self._tab_loot(nb)

        self.log = scrolledtext.ScrolledText(self.master, height=12, state=tk.DISABLED)
        self.log.pack(fill=tk.BOTH, side=tk.BOTTOM, padx=6, pady=(0, 6))

    def _tab_sources(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="源管理")

        ttk.Label(f, text="枪包源（1.1.4 文件夹/zip，或游戏根自动发现 tacz 子包）").grid(
            row=0, column=0, sticky=tk.W, padx=6, pady=4)

        bf = ttk.Frame(f)
        bf.grid(row=1, column=0, sticky=tk.W, padx=6, pady=2)
        ttk.Button(bf, text="添加文件夹", command=self._add_folder).pack(side=tk.LEFT, padx=2)
        ttk.Button(bf, text="添加 Zip", command=self._add_zip).pack(side=tk.LEFT, padx=2)
        ttk.Button(bf, text="添加游戏根", command=self._add_game_root).pack(side=tk.LEFT, padx=2)
        ttk.Button(bf, text="删除选中", command=self._del_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(bf, text="清空", command=self._clear_src).pack(side=tk.LEFT, padx=2)

        self.tree = ttk.Treeview(f, columns=("label", "path"), show="headings", height=10)
        self.tree.heading("label", text="标签")
        self.tree.heading("path", text="路径")
        self.tree.column("label", width=160)
        self.tree.column("path", width=560)
        self.tree.grid(row=2, column=0, sticky=tk.NSEW, padx=6, pady=4)
        sb = ttk.Scrollbar(f, orient=tk.VERTICAL, command=self.tree.yview)
        sb.grid(row=2, column=1, sticky=tk.NS)
        self.tree.configure(yscrollcommand=sb.set)

        f.columnconfigure(0, weight=1)
        f.rowconfigure(2, weight=1)

    def _tab_scan(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="扫描与链接")

        ttk.Button(f, text="扫描并链接所有源", command=self._run_scan).grid(
            row=0, column=0, sticky=tk.W, padx=6, pady=6)

        opt = ttk.LabelFrame(f, text="链接选项")
        opt.grid(row=1, column=0, sticky=tk.EW, padx=6, pady=4)
        ttk.Checkbutton(opt, text="严格(仅allow文件)", variable=self.var_strict).grid(row=0, column=0, padx=4)
        ttk.Checkbutton(opt, text="启发式", variable=self.var_heuristic).grid(row=0, column=1, padx=4)
        ttk.Checkbutton(opt, text="跨包(配件/弹药/裸id/tag)", variable=self.var_cross).grid(row=0, column=2, padx=4)
        ttk.Checkbutton(opt, text="开火自动(影响扩容)", variable=self.var_auto_fire).grid(row=0, column=3, padx=4)

        box = ttk.LabelFrame(f, text="已发现包（勾选参与生成，过多可滚动/搜索）")
        box.grid(row=2, column=0, sticky=tk.NSEW, padx=6, pady=4)
        self.pack_list = ScrollCheckList(box, height=280, on_change=self._on_pack_toggle)
        self.pack_list.pack(fill=tk.BOTH, expand=True)

        f.columnconfigure(0, weight=1)
        f.rowconfigure(2, weight=1)

    def _tab_loot(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="战利品生成")

        a = ttk.LabelFrame(f, text="附件池")
        a.grid(row=0, column=0, sticky=tk.EW, padx=6, pady=4)
        ttk.Label(a, text="Rolls").grid(row=0, column=0, padx=2)
        ttk.Spinbox(a, from_=0, to=10, textvariable=self.var_rolls, width=5).grid(row=0, column=1)
        ttk.Label(a, text="Min").grid(row=0, column=2, padx=2)
        ttk.Spinbox(a, from_=0, to=10, textvariable=self.var_roll_min, width=5).grid(row=0, column=3)
        ttk.Label(a, text="Max").grid(row=0, column=4, padx=2)
        ttk.Spinbox(a, from_=0, to=10, textvariable=self.var_roll_max, width=5).grid(row=0, column=5)
        ttk.Label(a, text="槽位过滤(空=全部,逗号分隔)").grid(row=1, column=0, padx=2, sticky=tk.W)
        ttk.Entry(a, textvariable=self.var_slots, width=40).grid(row=1, column=1, columnspan=5, sticky=tk.W)
        ttk.Checkbutton(a, text="自动开火加权扩容", variable=self.var_extmag_auto).grid(row=2, column=0, padx=2)
        ttk.Label(a, text="扩容权重加成").grid(row=2, column=1, padx=2)
        ttk.Spinbox(a, from_=0.0, to=10.0, increment=0.5, textvariable=self.var_extmag_bonus, width=5).grid(row=2, column=2)

        b = ttk.LabelFrame(f, text="弹药策略")
        b.grid(row=1, column=0, sticky=tk.EW, padx=6, pady=4)
        for txt, val in [("跳过该枪", "skip_gun"), ("不出弹药", "none"),
                         ("包默认弹容", "pack_default"), ("口径模糊", "caliber")]:
            ttk.Radiobutton(b, text=txt, variable=self.var_ammo, value=val).pack(side=tk.LEFT, padx=6)

        c = ttk.LabelFrame(f, text="输出")
        c.grid(row=2, column=0, sticky=tk.EW, padx=6, pady=4)
        ttk.Label(c, text="输出目录").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(c, textvariable=self.var_out, width=42).grid(row=0, column=1, padx=4)
        ttk.Button(c, text="浏览", command=self._choose_out).grid(row=0, column=2)
        ttk.Label(c, text="输出命名空间").grid(row=1, column=0, sticky=tk.W)
        ttk.Entry(c, textvariable=self.var_out_ns, width=20).grid(row=1, column=1, sticky=tk.W)
        ttk.Checkbutton(c, text="合并总表", variable=self.var_merge).grid(row=2, column=0, sticky=tk.W)
        ttk.Checkbutton(c, text="按原ns分表", variable=self.var_per_ns).grid(row=2, column=1, sticky=tk.W)
        ttk.Label(c, text="枪掉落概率0~1").grid(row=3, column=0, sticky=tk.W)
        ttk.Spinbox(c, from_=0.0, to=1.0, increment=0.05, textvariable=self.var_gun_chance, width=6).grid(row=3, column=1, sticky=tk.W)

        d = ttk.LabelFrame(f, text="无枪包单独输出(纯附件/纯弹药也可出表)")
        d.grid(row=4, column=0, sticky=tk.EW, padx=6, pady=4)
        ttk.Checkbutton(d, text="单独出配件池", variable=self.var_out_att).pack(side=tk.LEFT, padx=6)
        ttk.Checkbutton(d, text="单独出弹药池", variable=self.var_out_ammo).pack(side=tk.LEFT, padx=6)
        ttk.Label(d, text="合并/分表沿用上方合并总表、按原ns开关；纯枪包不受影响").pack(side=tk.LEFT, padx=6)

        ttk.Button(f, text="生成战利品表", command=self._generate).grid(row=5, column=0, sticky=tk.W, padx=6, pady=8)
        f.columnconfigure(0, weight=1)

    # ---------------- 源操作 ----------------
    def _add_folder(self):
        d = filedialog.askdirectory(title="选择1.1.4枪包文件夹")
        if not d:
            return
        if self.src.add(Path(d)):
            self._refresh_tree()
            self._log(f"添加文件夹: {d}")
        else:
            messagebox.showinfo("提示", "该源已存在或重复")

    def _add_zip(self):
        z = filedialog.askopenfilename(title="选择枪包zip", filetypes=[("Zip", "*.zip")])
        if not z:
            return
        if self.src.add(Path(z)):
            self._refresh_tree()
            self._log(f"添加Zip: {z}")
        else:
            messagebox.showinfo("提示", "该源已存在或重复")

    def _add_game_root(self):
        d = filedialog.askdirectory(title="选择游戏根(.minecraft 或版本根，含 tacz/)")
        if not d:
            return
        try:
            from tacz_io import discover_game_root
            roots = discover_game_root(Path(d)) or []
        except Exception as e:  # noqa
            roots = []
            self._log(f"discover_game_root失败: {e}")
        if not roots:
            cand = list(Path(d).rglob("gunpack.meta.json"))
            for m in cand:
                roots.append(m.parent)
        added = 0
        for r in roots:
            r = Path(r)
            label = (r.parent.name + "/" + r.name) if r.parent.name else r.name
            if self.src.add(r, label):
                added += 1
        self._refresh_tree()
        self._log(f"游戏根发现 {added} 个包" if added else "游戏根未发现1.1.4枪包")

    def _del_selected(self):
        for item in self.tree.selection():
            vals = self.tree.item(item, "values")
            if vals:
                self.src.remove(vals[1])
        self._refresh_tree()

    def _clear_src(self):
        self.src.clear()
        self._refresh_tree()
        self._log("已清空源")

    def _refresh_tree(self):
        for it in self.tree.get_children():
            self.tree.delete(it)
        for p, lbl in self.src.items():
            self.tree.insert("", tk.END, values=(lbl, str(p)))
        self.tree.update_idletasks()
        self.master.update_idletasks()

    def _choose_out(self):
        d = filedialog.askdirectory(title="输出目录")
        if d:
            self.var_out.set(d)

    # ---------------- 扫描链接 ----------------
    def _run_scan(self):
        if not self.src.paths:
            messagebox.showwarning("无源", "请先在“源管理”添加枪包")
            return
        self._cfg = ScanConfig(
            strict=self.var_strict.get(),
            heuristic=self.var_heuristic.get(),
            cross_pack=self.var_cross.get(),
            auto_fire=self.var_auto_fire.get(),
        )
        self._log("开始扫描...")

        def task():
            try:
                from tacz_io import iter_folder_json, iter_zip_json
                recs = []
                for p, lbl in self.src.items():
                    if p.is_dir():
                        recs += list(iter_folder_json(p))
                    elif p.suffix.lower() == ".zip":
                        recs += list(iter_zip_json(p))
                packs = tacz_scan.scan_records(recs)
                for p, lbl in self.src.items():
                    for ns, pm in packs.items():
                        if not getattr(pm, "source_label", None):
                            pm.source_label = lbl
                packs = tacz_link.link(packs, self._cfg)
                self._packs = packs
                self.master.after(0, self._fill_packs)
                self.master.after(0, lambda: self._log(f"扫描完成: {len(packs)} 个命名空间"))
            except Exception as exc:
                msg = str(exc)
                logger.exception("scan failed")
                self.master.after(0, lambda m=msg: messagebox.showerror("扫描失败", m))

        threading.Thread(target=task, daemon=True).start()

    def _fill_packs(self):
        if self.pack_list is None:
            return
        if not self._packs:
            self.pack_list.set_items([])
            return
        items = []
        self.pack_vars.clear()
        for ns, pm in self._packs.items():
            guns = len(getattr(pm, "guns", {}) or {})
            atts = len(getattr(pm, "attachments", {}) or {})
            ammos = len(getattr(pm, "ammos", {}) or {})
            self.pack_vars[ns] = tk.BooleanVar(value=True)
            items.append((ns, f"{ns}  枪{guns}/附件{atts}/弹药{ammos}"))
        self.pack_list.set_items(items)

    def _on_pack_toggle(self):
        if not self.pack_vars:
            return
        sel = set(self.pack_list.selected() if self.pack_list else [])
        for ns, v in self.pack_vars.items():
            v.set(ns in sel)

    # ---------------- 生成 ----------------
    def _generate(self):
        if not self._packs:
            messagebox.showwarning("未扫描", "请先扫描并链接")
            return
        if self.pack_list is not None:
            sel = self.pack_list.selected()
        else:
            sel = [ns for ns, v in self.pack_vars.items() if v.get()]
        if not sel:
            messagebox.showwarning("未勾选", "请勾选至少一个包")
            return

        slots = [s.strip().lower() for s in self.var_slots.get().split(",") if s.strip()]
        opt = CompatOptions(
            attachment_rolls=self.var_rolls.get(),
            min_rolls=self.var_roll_min.get(),
            max_rolls=self.var_roll_max.get(),
            slot_filter=slots,
            auto_fire_extmag=self.var_extmag_auto.get(),
            extmag_weight_bonus=float(self.var_extmag_bonus.get()),
            base_weight=1.0,
        )
        sub = {ns: self._packs[ns] for ns in sel if ns in self._packs}
        if not sub:
            messagebox.showwarning("无有效包", "勾选的包在扫描结果中不存在")
            return
        out = Path(self.var_out.get())
        out_ns = (self.var_out_ns.get().strip() or "tacz_loot").replace(" ", "_").lower()

        def task():
            try:
                written = write_loot(
                    sub, self._cfg, opt,
                    self.var_ammo.get(),
                    out, out_ns,
                    merge_total=self.var_merge.get(),
                    per_ns=self.var_per_ns.get(),
                    gun_roll_chance=float(self.var_gun_chance.get()),
                    out_att_only=self.var_out_att.get(),
                    out_ammo_only=self.var_out_ammo.get(),
                )
                n = len(written)
                self.master.after(0, lambda: self._log(f"生成完成: {n} 个文件 -> {out}"))
                if n:
                    self.master.after(0, lambda: messagebox.showinfo("完成", f"已写出到:\n{out}"))
                else:
                    self.master.after(0, lambda: messagebox.showinfo("完成", "无内容可写（包可能全空）"))
            except Exception as exc:
                msg = str(exc)
                logger.exception("generate failed")
                self.master.after(0, lambda m=msg: messagebox.showerror("生成失败", m))

        threading.Thread(target=task, daemon=True).start()
        self._log("开始生成...")

    # ---------------- 日志（不回环） ----------------
    def _append_log(self, msg: str):
        try:
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, msg + "\n")
            self.log.see(tk.END)
        finally:
            self.log.configure(state=tk.DISABLED)

    def _log(self, msg: str):
        self._append_log(msg)

    def _install_log_handler(self):
        class UIHandler(logging.Handler):
            def __init__(self, cb):
                super().__init__()
                self.cb = cb

            def emit(self, record):
                try:
                    text = self.format(record)
                except Exception:
                    text = record.getMessage()
                try:
                    self.cb(text)
                except Exception:
                    pass

        h = UIHandler(self._append_log)
        h.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
        h.setLevel(logging.INFO)
        logging.getLogger().addHandler(h)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()