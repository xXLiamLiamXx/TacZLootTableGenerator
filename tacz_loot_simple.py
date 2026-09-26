"""TACZ 多源跨包战利品生成器（全源先建索引，再统一回灌allow）。
阶段1：遍历所有已选源 -> 全局附件表 + 全局tag表
阶段2：再扫所有枪 data(ammo/ammo_amount) + 用完整全局索引回灌 allow
不按槽位过滤；每枪单独loot；去_data；SEMI硬编码；附件等权；ammo用data.ammo+ammo_amount。
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
import tkinter as tk

try:
    import json5
except Exception:
    json5 = None

LOG = logging.getLogger("tacz_loot_simple")
LOOT_DIR_DEFAULT = "loot_tables"  # 1.20-老版改 "loot_tables"


# ---------------- json 抗注释 ----------------
def load_json_text(text: str):
    if json5 is not None:
        try:
            return json5.loads(text)
        except Exception:
            pass
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    out = []
    for line in text.splitlines():
        res = []
        in_str = False
        esc = False
        if line.lstrip().startswith("//"):
            continue
        for i, ch in enumerate(line):
            if esc:
                res.append(ch); esc = False; continue
            if ch == "\\":
                res.append(ch); esc = True; continue
            if ch == '"':
                in_str = not in_str
                res.append(ch); continue
            if not in_str:
                if ch == "#":
                    break
                if ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                    break
            res.append(ch)
        out.append("".join(res))
    cleaned = re.sub(r",(\s*[}\]])", r"\1", "\n".join(out))
    return json.loads(cleaned)


def load_json_file(p: Path):
    return load_json_text(p.read_text(encoding="utf-8", errors="ignore"))


# ---------------- 模型 ----------------
@dataclass
class AttachmentRec:
    full_id: str
    name: str = ""
    atype: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class GunRec:
    ns: str
    gun_id: str
    full_id: str
    name: str = ""
    ammo_full: str = ""
    ammo_amount: int = 0
    ext_mag_amount: int = 0
    fire_mode_data: str = ""
    attachments: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)
    data_raw: dict = field(default_factory=dict)


@dataclass
class PackRec:
    ns: str
    root: Path
    guns: dict = field(default_factory=dict)


# 全局索引（所有已选源合并）
G_ATT: dict[str, AttachmentRec] = {}                 # full_id -> rec
G_TAG: dict[tuple[str, str], list[str]] = {}         # (ns, tagname) -> entries
# 同tag名跨ns兜底开关
CROSS_NS_SAME_TAG = True


def reset_global_index():
    G_ATT.clear()
    G_TAG.clear()


# ---------------- 发现ns ----------------
def discover_ns_roots(folder: Path):
    folder = folder.resolve()
    res = []
    d = folder / "data"
    if d.is_dir():
        for sub in sorted(d.iterdir()):
            if sub.is_dir():
                res.append((sub.name, sub))
    if (folder / "data" / "attachments").is_dir() or (folder / "tacz_tags").is_dir() or (folder / "data" / "guns").is_dir():
        if (folder / "data").is_dir():
            res.append((folder.name, folder / "data"))
    return res


def read_tag_entries(obj):
    if obj is None:
        return []
    if isinstance(obj, list):
        return [str(x) for x in obj]
    if isinstance(obj, dict):
        if isinstance(obj.get("values"), list):
            return [str(x) for x in obj["values"]]
        for v in obj.values():
            if isinstance(v, list):
                return [str(x) for x in v]
    if isinstance(obj, str):
        return [obj]
    return []


# ---------------- 阶段1：全源附件+tag ----------------
def index_all_attachments_and_tags(folder: Path):
    for ns, datadir in discover_ns_roots(folder):
        # attachments
        att_dir = datadir / "data" / "attachments"
        if att_dir.is_dir():
            for jp in sorted(att_dir.rglob("*.json")):
                try:
                    obj = load_json_file(jp)
                except Exception as e:
                    LOG.warning("附件解析失败 %s: %s", jp, e)
                    continue
                base = jp.stem
                if base.endswith("_data"):
                    base = base[:-5]
                fid = f"{ns}:{base}"
                rec = G_ATT.get(fid) or AttachmentRec(full_id=fid)
                if isinstance(obj, dict):
                    rec.name = obj.get("name", rec.name)
                    rec.atype = obj.get("type", rec.atype)
                    if not rec.raw:
                        rec.raw = obj
                G_ATT.setdefault(fid, rec)
        idx_dir = datadir / "index" / "attachments"
        if idx_dir.is_dir():
            for jp in sorted(idx_dir.rglob("*.json")):
                try:
                    obj = load_json_file(jp)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                fid = f"{ns}:{jp.stem}"
                rec = G_ATT.get(fid) or AttachmentRec(full_id=fid)
                rec.name = obj.get("name", rec.name)
                rec.atype = obj.get("type", rec.atype)
                if not rec.raw:
                    rec.raw = obj
                G_ATT[fid] = rec

        # tags
        for troot in ("tacz_tags/attachments", "tags/attachments"):
            tagroot = datadir / troot
            if not tagroot.is_dir():
                continue
            for jp in sorted(tagroot.rglob("*.json")):
                rel = jp.relative_to(tagroot).with_suffix("")
                tagname = str(rel).replace("\\", "/")
                try:
                    obj = load_json_file(jp)
                except Exception as e:
                    LOG.warning("tag解析失败 %s: %s", jp, e)
                    continue
                key = (ns, tagname)
                # 同路径同名tag全部收集（TACZ会合并多个包的同名tag）
                cur = G_TAG.get(key, [])
                for x in read_tag_entries(obj):
                    if x not in cur:
                        cur.append(x)
                G_TAG[key] = cur


# ---------------- 阶段2：枪data + 全局回灌allow ----------------
def scan_guns_and_allow(folder: Path, packs: dict):
    for ns, datadir in discover_ns_roots(folder):
        pk = packs.setdefault(ns, PackRec(ns=ns, root=folder))

        gun_dir = datadir / "data" / "guns"
        if gun_dir.is_dir():
            for jp in sorted(gun_dir.rglob("*_data.json")):
                gid = jp.stem[:-5]
                try:
                    obj = load_json_file(jp)
                except Exception as e:
                    LOG.warning("枪data失败 %s: %s", jp, e)
                    obj = {}
                if not isinstance(obj, dict):
                    obj = {}
                g = pk.guns.get(gid) or GunRec(ns=ns, gun_id=gid, full_id=f"{ns}:{gid}")
                g.data_raw = obj
                g.name = obj.get("name", g.name)
                ammo = obj.get("ammo", "")
                if ammo:
                    g.ammo_full = ammo if ":" in ammo else f"{ns}:{ammo}"
                amt = obj.get("ammo_amount", None)
                if amt is None and isinstance(obj.get("ammo"), dict):
                    amt = obj["ammo"].get("amount")
                try:
                    g.ammo_amount = int(amt) if amt not in (None, "") else 0
                except Exception:
                    g.ammo_amount = 0
                ext = obj.get("extended_mag_ammo_amount", None)
                try:
                    g.ext_mag_amount = int(ext) if ext not in (None, "") else 0
                except Exception:
                    g.ext_mag_amount = 0
                fm = obj.get("fire_mode", "")
                if isinstance(fm, list) and fm:
                    fm = fm[0]
                g.fire_mode_data = str(fm)
                pk.guns[gid] = g

        for aroot in ("tacz_tags/attachments/allow_attachments", "tags/attachments/allow_attachments"):
            allowroot = datadir / aroot
            if not allowroot.is_dir():
                continue
            for jp in sorted(allowroot.glob("*.json")):
                gid = jp.stem
                if gid.endswith("_data"):
                    gid = gid[:-5]
                g = pk.guns.get(gid) or GunRec(ns=ns, gun_id=gid, full_id=f"{ns}:{gid}")
                try:
                    obj = load_json_file(jp)
                except Exception as e:
                    LOG.warning("allow解析失败 %s: %s", jp, e)
                    obj = []
                resolved, unresolved = expand_allow(read_tag_entries(obj), ns, set())
                for fid in resolved:
                    if fid not in g.attachments:
                        g.attachments.append(fid)
                for u in unresolved:
                    if u not in g.unresolved:
                        g.unresolved.append(u)
                pk.guns[gid] = g


def expand_allow(entries, home_ns: str, visited, depth=0):
    resolved, unresolved = [], []
    if depth > 30:
        return resolved, unresolved
    for raw in entries or []:
        r = (raw or "").strip()
        if not r:
            continue
        if r.startswith("#"):
            tref = r[1:]
            tns, _, tname = tref.partition(":")
            tns = tns or home_ns
            key = (tns, tname)
            if key in visited:
                continue
            visited.add(key)
            sub = G_TAG.get(key)
            if not sub and CROSS_NS_SAME_TAG:
                # 同tag名跨ns兜底（不指定ns时更激进；指定ns只兜底同tag名全部ns可关）
                fallback = []
                for (kns, kn), vals in G_TAG.items():
                    if kn == tname:
                        for x in vals:
                            if x not in fallback:
                                fallback.append(x)
                sub = fallback
            if not sub:
                unresolved.append(r)
                continue
            r2, u2 = expand_allow(sub, tns, visited, depth + 1)
            for x in r2:
                if x not in resolved:
                    resolved.append(x)
            for x in u2:
                if x not in unresolved:
                    unresolved.append(x)
        else:
            fid = r if ":" in r else f"{home_ns}:{r}"
            if fid in G_ATT:
                if fid not in resolved:
                    resolved.append(fid)
            else:
                # 精确ns失败 -> 全全局按base模糊（跨包）
                base = fid.split(":", 1)[-1].lower()
                hits = [a for a in G_ATT if a.split(":", 1)[-1].lower() == base]
                if hits:
                    hits.sort()
                    for h in hits:
                        if h not in resolved:
                            resolved.append(h)
                    if len(hits) > 1:
                        LOG.info("附件base模糊命中多ns %s -> %s", fid, hits)
                else:
                    unresolved.append(fid)
    return resolved, unresolved


# ---------------- loot ----------------
def build_loot(g: GunRec, rolls: int, chance: float):
    pools = []
    gun = {
        "type": "minecraft:item",
        "name": "tacz:modern_kinetic_gun",
        "functions": [{"function": "minecraft:set_nbt",
                       "tag": f'{{GunId:"{g.full_id}",GunFireMode:"SEMI"}}'}],
    }
    if chance < 1.0:
        pools.append({"rolls": 1,
                      "conditions": [{"condition": "minecraft:random_chance", "chance": float(chance)}],
                      "entries": [gun]})
    else:
        pools.append({"rolls": 1, "entries": [gun]})

    if g.attachments:
        pools.append({"rolls": max(0, int(rolls)),
                      "entries": [{
                          "type": "minecraft:item",
                          "name": "tacz:attachment",
                          "weight": 1,
                          "functions": [{"function": "minecraft:set_nbt", "tag": f'{{AttachmentId:"{fid}"}}'}]
                      } for fid in g.attachments]})

    if g.ammo_full:
        amount = g.ammo_amount or 1
        funcs = [{"function": "minecraft:set_nbt", "tag": f'{{AmmoId:"{g.ammo_full}"}}'}]
        funcs.append({"function": "minecraft:set_count",
                      "count": {"min": amount, "max": amount} if amount > 1 else 1})
        pools.append({"rolls": 1, "entries": [{
            "type": "minecraft:item", "name": "tacz:ammo", "functions": funcs}]})
    return {"pools": pools}


def write_gun(g: GunRec, out_root: Path, loot_ns: str, rolls: int, sub: str, chance: float):
    table = build_loot(g, rolls, chance)
    rel = Path(sub) / "data" / loot_ns / LOOT_DIR_DEFAULT / "guns" / g.ns / f"{g.gun_id}.json"
    f = out_root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
    return f, len(g.unresolved)


# ---------------- GUI ----------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TACZ 战利品（全源先索引/再回灌/跨包）")
        self.geometry("1060x780")
        self.folders: list[Path] = []
        self.packs: dict[str, PackRec] = {}
        self.v_rolls = tk.IntVar(value=2)
        self.v_out = tk.StringVar(value=str(Path.home() / "Desktop" / "tacz_loot_out"))
        self.v_lootns = tk.StringVar(value="tacz_loot")
        self.v_sub = tk.StringVar(value=".")
        self.v_chance = tk.DoubleVar(value=1.0)
        self._build()
        self._install_log()

    def _build(self):
        bar = ttk.Frame(self)
        bar.pack(side=tk.TOP, fill=tk.X, padx=6, pady=4)
        ttk.Button(bar, text="添加文件夹", command=self._add_one).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="添加父目录(含子文件夹)", command=self._add_parent).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="移除选中源", command=self._remove_src).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="清空源", command=self._clear).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="扫描全部源(全索引后回灌)", command=self._scan).pack(side=tk.LEFT, padx=10)
        ttk.Button(bar, text="生成选中枪", command=self._gen_sel).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="生成全部枪", command=self._gen_all).pack(side=tk.LEFT, padx=2)

        mid = ttk.Frame(self)
        mid.pack(side=tk.TOP, fill=tk.X, padx=6, pady=2)
        src = ttk.LabelFrame(mid, text="已选源（全源合并索引）")
        src.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.src_list = tk.Listbox(src, height=4, selectmode=tk.EXTENDED)
        self.src_list.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, pady=4)
        ttk.Scrollbar(src, orient=tk.VERTICAL, command=self.src_list.yview).pack(side=tk.RIGHT, fill=tk.Y)
        self.src_list.configure(yscrollcommand=lambda *a: None)

        opt = ttk.LabelFrame(mid, text="选项")
        opt.pack(side=tk.LEFT, fill=tk.Y)
        ttk.Label(opt, text="附件rolls").grid(row=0, column=0, padx=2, pady=2, sticky=tk.W)
        ttk.Spinbox(opt, from_=0, to=20, textvariable=self.v_rolls, width=5).grid(row=0, column=1)
        ttk.Label(opt, text="loot_ns").grid(row=0, column=2, padx=2, sticky=tk.W)
        ttk.Entry(opt, textvariable=self.v_lootns, width=12).grid(row=0, column=3)
        ttk.Label(opt, text="子目录前缀").grid(row=1, column=0, padx=2, pady=2, sticky=tk.W)
        ttk.Entry(opt, textvariable=self.v_sub, width=12).grid(row=1, column=1)
        ttk.Label(opt, text="枪概率").grid(row=1, column=2, padx=2, sticky=tk.W)
        ttk.Spinbox(opt, from_=0.0, to=1.0, increment=0.05, textvariable=self.v_chance, width=6).grid(row=1, column=3)
        ttk.Label(opt, text="输出根").grid(row=2, column=0, padx=2, pady=2, sticky=tk.W)
        ttk.Entry(opt, textvariable=self.v_out, width=22).grid(row=2, column=1, columnspan=2)
        ttk.Button(opt, text="浏览", command=self._browse_out).grid(row=2, column=3, padx=2)

        pane = ttk.Panedwindow(self, orient=tk.VERTICAL)
        pane.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=6, pady=4)
        tf = ttk.Frame(pane)
        self.tree = ttk.Treeview(tf, columns=("ammo", "att", "warn"), show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="包/枪")
        self.tree.column("#0", width=320)
        self.tree.heading("ammo", text="ammo"); self.tree.column("ammo", width=190)
        self.tree.heading("att", text="配件数"); self.tree.column("att", width=90)
        self.tree.heading("warn", text="未解析"); self.tree.column("warn", width=270)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        pane.add(tf, weight=3)
        lf = ttk.Frame(pane)
        self.log = scrolledtext.ScrolledText(lf, state=tk.DISABLED)
        self.log.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        pane.add(lf, weight=1)

    def _install_log(self):
        class H(logging.Handler):
            def __init__(self, app):
                super().__init__()
                self.app = app
                self.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
            def emit(self, rec):
                msg = self.format(rec)
                self.app.after(0, lambda m=msg: self._log(m))
        logging.getLogger().addHandler(H(self))
        logging.getLogger().setLevel(logging.INFO)

    def _log(self, s):
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, s + "\n"); self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _refresh_src(self):
        self.src_list.delete(0, tk.END)
        for p in self.folders:
            self.src_list.insert(tk.END, str(p))

    def _add_one(self):
        d = filedialog.askdirectory(title="选择解压枪包文件夹")
        if d and Path(d) not in self.folders:
            self.folders.append(Path(d)); self._refresh_src()

    def _add_parent(self):
        par = filedialog.askdirectory(title="选择父目录：本身+直接子文件夹都加为源")
        if not par:
            return
        par = Path(par)
        for cand in [par] + [p for p in sorted(par.iterdir()) if p.is_dir()]:
            if cand not in self.folders:
                self.folders.append(cand)
        self._refresh_src()

    def _remove_src(self):
        for i in reversed(self.src_list.curselection()):
            try:
                self.folders.pop(i)
            except IndexError:
                pass
        self._refresh_src()

    def _clear(self):
        self.folders.clear(); self.packs.clear(); reset_global_index()
        self.src_list.delete(0, tk.END); self.tree.delete(*self.tree.get_children())

    def _browse_out(self):
        d = filedialog.askdirectory(title="输出根目录")
        if d:
            self.v_out.set(d)

    def _scan(self):
        if not self.folders:
            messagebox.showwarning("无源", "先添加文件夹/父目录")
            return
        self.tree.delete(*self.tree.get_children())
        self.packs.clear(); reset_global_index()

        def task():
            try:
                # 阶段1：所有源全量附件+tag
                for f in list(self.folders):
                    self.after(0, lambda m=f: self._log(f"[index] 附件/tag {m}"))
                    index_all_attachments_and_tags(Path(f))
                self.after(0, lambda: self._log(
                    f"[index] 全局附件={len(G_ATT)} 全局tag={len(G_TAG)}"))
                # 阶段2：所有源枪data + 用全局索引回灌allow
                for f in list(self.folders):
                    self.after(0, lambda m=f: self._log(f"[guns] data+allow {m}"))
                    scan_guns_and_allow(Path(f), self.packs)
                self.after(0, self._fill_tree)
                self.after(0, lambda: self._log("[done] 全源扫描完成，已按全局索引回灌allow"))
            except Exception as e:
                LOG.exception("scan error")
                self.after(0, lambda m=str(e): messagebox.showerror("扫描失败", m))
        threading.Thread(target=task, daemon=True).start()

    def _fill_tree(self):
        for ns, pk in sorted(self.packs.items()):
            node = self.tree.insert("", tk.END, text=f"{ns}（{len(pk.guns)}枪）", values=("", "", ""))
            for gid, g in sorted(pk.guns.items()):
                warn = ",".join(g.unresolved) if g.unresolved else ""
                self.tree.insert(node, tk.END, text=gid,
                                values=(g.ammo_full or "(无ammo)", str(len(g.attachments)), warn))

    def _selected(self):
        out = []
        for iid in self.tree.selection():
            txt = self.tree.item(iid, "text")
            par = self.tree.parent(iid)
            if par:
                ns = self.tree.item(par, "text").split("（")[0]
                g = self.packs.get(ns, PackRec(ns=ns, root=Path("."))).guns.get(txt)
                if g:
                    out.append(g)
        return out

    def _gen_sel(self):
        gs = self._selected()
        if not gs:
            messagebox.showwarning("未选择", "展开ns后多选枪")
            return
        self._gen(gs)

    def _gen_all(self):
        if not self.packs:
            messagebox.showwarning("未扫描", "先扫描")
            return
        self._gen([g for pk in self.packs.values() for g in pk.guns.values()])

    def _gen(self, guns):
        out = Path(self.v_out.get())
        ns = self.v_lootns.get().strip() or "tacz_loot"
        sub = self.v_sub.get().strip() or "."
        rolls = self.v_rolls.get()
        chance = self.v_chance.get()

        def task():
            n = 0
            for g in guns:
                try:
                    f, w = write_gun(g, out, ns, rolls, sub, chance)
                    n += 1
                    self.after(0, lambda m=f: self._log(f"[write] {m} 未解析={w}"))
                except Exception as e:
                    LOG.exception("gen error")
                    self.after(0, lambda m=str(e): self._log(f"[err] {g.full_id} {m}"))
            self.after(0, lambda: self._log(f"[done] 生成 {n} 把枪 -> {out}"))
        threading.Thread(target=task, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()
