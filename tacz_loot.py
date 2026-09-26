"""标准原版 loot_table；空类防护 + 无枪包可单独出附件/弹药池。
签名兼容 main_gui 新调用：out_att_only / out_ammo_only。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List

from models import ScanConfig, PackModel
from tacz_compat import CompatOptions, build_all_plans, LootAttachmentPlan
from tacz_ammo import build_all_ammo_plans, AmmoPlan

logger = logging.getLogger("tacz_loot")


def _gun_entry(gun_full_id: str, fire_mode: str) -> dict:
    return {
        "type": "minecraft:item",
        "name": "tacz:modern_kinetic_gun",
        "functions": [
            {"function": "minecraft:set_nbt",
             "tag": '{GunId:"%s",GunFireMode:"%s"}' % (gun_full_id, fire_mode)}
        ]
    }


def _single_gun_table(gun_full_id, fire_mode, att_plan: LootAttachmentPlan,
                      ammo_plan: AmmoPlan, gun_roll_chance: float = 1.0) -> dict:
    pools = []
    gp = {"rolls": 1, "entries": [_gun_entry(gun_full_id, fire_mode)]}
    if gun_roll_chance < 1.0:
        gp["conditions"] = [{"condition": "minecraft:random_chance", "chance": float(gun_roll_chance)}]
    pools.append(gp)

    if att_plan is not None and att_plan.entries:
        rolls = att_plan.rolls if att_plan.rolls > 0 else 1
        pools.append({"rolls": rolls, "entries": att_plan.to_loot_entries()})

    ae = ammo_plan.to_loot_entry() if ammo_plan is not None else None
    if ae:
        pools.append({"rolls": 1, "entries": [ae]})
    return {"pools": pools}


def _merged_table(guns, plans_att, plans_ammo, fire_map) -> dict:
    pools = []
    if guns:
        pools.append({"rolls": 1, "entries": [_gun_entry(g, fire_map.get(g, "SEMI")) for g in guns]})

    att_seen, roll_cands = {}, []
    for g in guns:
        p = plans_att.get(g)
        if not p or not p.entries:
            continue
        roll_cands.append(p.rolls)
        for e in p.entries:
            att_seen[e["full_id"]] = max(att_seen.get(e["full_id"], 0), e["weight"])
    if att_seen:
        entries = [{"type": "minecraft:item", "name": "tacz:attachment",
                    "functions": [{"function": "minecraft:set_nbt", 'tag': '{AttachmentId:"%s"}' % fid}],
                    "weight": max(1, int(w))} for fid, w in att_seen.items()]
        rolls = max(roll_cands) if roll_cands else 1
        pools.append({"rolls": max(1, min(rolls, 5)), "entries": entries})

    ammo_seen = {}
    for g in guns:
        p = plans_ammo.get(g)
        if not p or not p.to_loot_entry():
            continue
        cur = ammo_seen.get(p.ammo_full_id)
        if not cur or (p.count_max, p.count_min) > (cur.count_max, cur.count_min):
            ammo_seen[p.ammo_full_id] = p
    if ammo_seen:
        pools.append({"rolls": 1, "entries": [p.to_loot_entry() for p in ammo_seen.values()]})
    return {"pools": pools}


def _att_only_table(pm: PackModel, opt: CompatOptions) -> dict:
    slotf = {s.lower() for s in (opt.slot_filter or [])}
    entries = []
    for fid, a in pm.attachments.items():
        t = (a.type or "misc")
        if slotf and t not in slotf:
            continue
        w = opt.base_weight
        if a.weight:
            w = max(opt.base_weight, a.weight * 10)
        if t == "extended_mag":
            w += opt.extmag_weight_bonus
        entries.append({"full_id": fid, "weight": w})
    n = len(entries)
    if n == 0:
        return None
    rolls = opt.attachment_rolls if opt.attachment_rolls > 0 else 1
    rolls = max(opt.min_rolls, rolls)
    rolls = min(rolls, opt.max_rolls)
    if rolls > n:
        rolls = n
    if rolls < 1:
        rolls = 1
    loot = [{"type": "minecraft:item", "name": "tacz:attachment",
             "functions": [{"function": "minecraft:set_nbt", 'tag': '{AttachmentId:"%s"}' % e["full_id"]}],
             "weight": max(1, int(e["weight"]))} for e in entries]
    return {"pools": [{"rolls": rolls, "entries": loot}]}


def _ammo_only_table(pm: PackModel) -> dict:
    entries = []
    for fid, am in pm.ammos.items():
        raw = am.data_raw or am.index_raw or {}
        lo = hi = 0
        for k in ("stack_size", "max_stack_size", "amount"):
            v = raw.get(k)
            if isinstance(v, int) and v > 0:
                lo, hi = max(1, v // 2), v
                break
        if hi <= 0:
            lo, hi = 8, 16
        funcs = [{"function": "minecraft:set_nbt", 'tag': '{AmmoId:"%s"}' % fid}]
        if hi > 0:
            funcs.append({"function": "minecraft:set_count", "count": {"min": lo, "max": hi}})
        entries.append({"type": "minecraft:item", "name": "tacz:ammo", "functions": funcs})
    if not entries:
        return None
    return {"pools": [{"rolls": 1, "entries": entries}]}


def write_loot(packs, cfg: ScanConfig, opt: CompatOptions, ammo_policy: str,
               out_root: Path, out_ns: str,
               merge_total: bool = False,
               per_ns: bool = True,
               gun_roll_chance: float = 1.0,
               out_att_only: bool = False,
               out_ammo_only: bool = False) -> List[Path]:
    out_root = Path(out_root)
    plans_att = build_all_plans(packs, cfg, opt)
    plans_ammo = build_all_ammo_plans(packs, cfg, ammo_policy)
    fire_map = {g.full_id: (g.fire_mode or "SEMI") for pm in packs.values() for g in pm.guns.values()}
    written: List[Path] = []

    def has_content(pm: PackModel) -> bool:
        return bool(pm.guns or pm.attachments or pm.ammos)

    active = {ns: pm for ns, pm in packs.items() if has_content(pm)}
    if not active:
        logger.warning("无可用包（枪/配件/弹药均为空），未生成任何 loot 表")
        return written

    # ---- 单独出配件池 ----
    if out_att_only:
        if merge_total:
            slotf = {s.lower() for s in (opt.slot_filter or [])}
            combined_w = {}
            for pm in active.values():
                for fid, a in pm.attachments.items():
                    t = (a.type or "misc")
                    if slotf and t not in slotf:
                        continue
                    w = opt.base_weight
                    if a.weight:
                        w = max(opt.base_weight, a.weight * 10)
                    if t == "extended_mag":
                        w += opt.extmag_weight_bonus
                    combined_w[fid] = max(combined_w.get(fid, 0), w)
            if combined_w:
                n = len(combined_w)
                rolls = opt.attachment_rolls if opt.attachment_rolls > 0 else 1
                rolls = max(opt.min_rolls, rolls)
                rolls = min(rolls, opt.max_rolls)
                if rolls > n:
                    rolls = n
                if rolls < 1:
                    rolls = 1
                ents = [{"type": "minecraft:item", "name": "tacz:attachment",
                         "functions": [{"function": "minecraft:set_nbt", 'tag': '{AttachmentId:"%s"}' % fid}],
                         "weight": max(1, int(w))} for fid, w in combined_w.items()]
                d = out_root / "data" / out_ns / "loot_table" / "tacz_loot" / "attachments_all.json"
                d.parent.mkdir(parents=True, exist_ok=True)
                d.write_text(json.dumps({"pools": [{"rolls": rolls, "entries": ents}]}, ensure_ascii=False, indent=2), encoding="utf-8")
                written.append(d)
        else:
            for ns, pm in active.items():
                if not pm.attachments:
                    continue
                tbl = _att_only_table(pm, opt)
                if not tbl:
                    continue
                target = ns if per_ns else out_ns
                fname = "attachments.json" if per_ns else "attachments_%s.json" % ns
                d = out_root / "data" / target / "loot_table" / "tacz_loot" / fname
                d.parent.mkdir(parents=True, exist_ok=True)
                d.write_text(json.dumps(tbl, ensure_ascii=False, indent=2), encoding="utf-8")
                written.append(d)

    # ---- 单独出弹药池 ----
    if out_ammo_only:
        if merge_total:
            combined = {}
            for pm in active.values():
                for fid, am in pm.ammos.items():
                    combined.setdefault(fid, am)
            if combined:
                entries = []
                for fid, am in combined.items():
                    raw = am.data_raw or am.index_raw or {}
                    lo = hi = 0
                    for k in ("stack_size", "max_stack_size", "amount"):
                        v = raw.get(k)
                        if isinstance(v, int) and v > 0:
                            lo, hi = max(1, v // 2), v
                            break
                    if hi <= 0:
                        lo, hi = 8, 16
                    funcs = [{"function": "minecraft:set_nbt", 'tag': '{AmmoId:"%s"}' % fid}]
                    if hi > 0:
                        funcs.append({"function": "minecraft:set_count", "count": {"min": lo, "max": hi}})
                    entries.append({"type": "minecraft:item", "name": "tacz:ammo", "functions": funcs})
                d = out_root / "data" / out_ns / "loot_table" / "tacz_loot" / "ammo_all.json"
                d.parent.mkdir(parents=True, exist_ok=True)
                d.write_text(json.dumps({"pools": [{"rolls": 1, "entries": entries}]}, ensure_ascii=False, indent=2), encoding="utf-8")
                written.append(d)
        else:
            for ns, pm in active.items():
                if not pm.ammos:
                    continue
                tbl = _ammo_only_table(pm)
                if not tbl:
                    continue
                target = ns if per_ns else out_ns
                fname = "ammo.json" if per_ns else "ammo_%s.json" % ns
                d = out_root / "data" / target / "loot_table" / "tacz_loot" / fname
                d.parent.mkdir(parents=True, exist_ok=True)
                d.write_text(json.dumps(tbl, ensure_ascii=False, indent=2), encoding="utf-8")
                written.append(d)

    # ---- 常规枪表 ----
    if merge_total:
        all_guns = [g.full_id for pm in active.values() for g in pm.guns.values()]
        if all_guns:
            table = _merged_table(all_guns, plans_att, plans_ammo, fire_map)
            if table["pools"]:
                d = out_root / "data" / out_ns / "loot_table" / "tacz_loot" / "all_guns.json"
                d.parent.mkdir(parents=True, exist_ok=True)
                d.write_text(json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
                written.append(d)
    else:
        for ns, pm in active.items():
            target = ns if per_ns else out_ns
            for g in pm.guns.values():
                att_plan = plans_att.get(g.full_id) or LootAttachmentPlan(gun_full_id=g.full_id, fire_mode=g.fire_mode or "SEMI")
                ammo_plan = plans_ammo.get(g.full_id) or AmmoPlan(gun_full_id=g.full_id, policy=ammo_policy)
                table = _single_gun_table(g.full_id, g.fire_mode or "SEMI", att_plan, ammo_plan, gun_roll_chance)
                d = out_root / "data" / target / "loot_table" / "tacz_loot" / f"{g.gun_id}.json"
                d.parent.mkdir(parents=True, exist_ok=True)
                d.write_text(json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
                written.append(d)
            if not pm.guns:
                logger.info("包 %s 仅含附件/弹药，未勾选单独出池时分枪模式跳过", ns)

    meta = out_root / "pack.mcmeta"
    if not meta.exists():
        meta.write_text(json.dumps({"pack": {"pack_format": 18, "description": "TACZ loot generator output"}},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(meta)
    return written