"""兼容策略层：在 link 已解析 resolved_attachments 基础上，按开关裁成最终掉落池。
输入 packs(已link) + ScanConfig + 每枪/全局选项；输出每把枪的 LootAttachmentPlan。
不读文件、不重新扫描。
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List

from models import PackModel, GunInfo, ScanConfig

logger = logging.getLogger("tacz_compat")

SLOT_TYPES = ("scope", "stock", "muzzle", "grip", "laser", "extended_mag")


@dataclass
class CompatOptions:
    attachment_rolls: int = 2          # 附件单池随机抽几个
    min_rolls: int = 1
    max_rolls: int = 3
    slot_filter: List[str] = field(default_factory=list)   # 非空则只保留这些槽，如 ["scope","muzzle"]
    auto_fire_extmag: bool = True       # 开火自动且启用扩容时，优先把 extended_mag 放进池
    extmag_weight_bonus: float = 2.0    # 扩容弹匣相对其它附件的权重加成
    base_weight: float = 1.0


@dataclass
class LootAttachmentPlan:
    gun_full_id: str
    fire_mode: str
    entries: List[dict] = field(default_factory=list)   # 每个 {full_id,type,weight}
    rolls: int = 1

    def to_loot_entries(self):
        out = []
        for e in self.entries:
            out.append({
                "type": "minecraft:item",
                "name": "tacz:attachment",
                "functions": [
                    {"function": "minecraft:set_nbt",
                     "tag": "{AttachmentId:\"%s\"}" % e["full_id"]}
                ],
                "weight": max(1, int(e["weight"]))
            })
        return out


def _att_type(packs: Dict[str, PackModel], full_id: str) -> str:
    ns, _, gid = full_id.partition(":")
    pm = packs.get(ns)
    if pm:
        a = pm.attachments.get(full_id)
        if a:
            return a.type
    for pm in packs.values():
        a = pm.attachments.get(full_id)
        if a:
            return a.type
    return "misc"


def build_attachment_plan(g: GunInfo, pm: PackModel, packs: Dict[str, PackModel],
                          cfg: ScanConfig, opt: CompatOptions) -> LootAttachmentPlan:
    plan = LootAttachmentPlan(gun_full_id=g.full_id, fire_mode=g.fire_mode)

    ids = list(g.resolved_attachments or [])
    if cfg.strict:
        # 严格：仅保留 allow 文件具体id + exclusive，不按category/type盲补（link已保证）
        pass
    # 槽位过滤
    if opt.slot_filter:
        keep = {s.lower() for s in opt.slot_filter}
        ids = [i for i in ids if _att_type(packs, i) in keep]

    # 去重保序
    seen = set()
    uniq = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            uniq.append(i)
    ids = uniq

    # 开火自动 -> 扩容弹匣加权/强制纳入
    if opt.auto_fire_extmag and cfg.auto_fire and g.fire_mode == "AUTO":
        ext = [i for i in ids if _att_type(packs, i) == "extended_mag"]
        if not ext:
            # 同/跨包找扩容补进池（受严格/跨包约束）
            srcs = packs.values() if cfg.cross_pack else [pm]
            for sp in srcs:
                for a in sp.attachments.values():
                    at = "extended_mag" if a.type in ("mag", "extended_mag") else a.type
                    if at == "extended_mag":
                        ids.append(a.full_id)
                        ext.append(a.full_id)
                        break
                if ext:
                    break

    for i in ids:
        t = _att_type(packs, i)
        w = opt.base_weight
        aobj = None
        ns, _, gid = i.partition(":")
        if pm.attachments.get(i):
            aobj = pm.attachments[i]
        else:
            for sp in (packs.values() if cfg.cross_pack else [pm]):
                if sp.attachments.get(i):
                    aobj = sp.attachments[i]; break
        if aobj and aobj.weight:
            w = max(opt.base_weight, aobj.weight * 10)
        if t == "extended_mag" and g.fire_mode == "AUTO" and opt.auto_fire_extmag:
            w += opt.extmag_weight_bonus
        plan.entries.append({"full_id": i, "type": t, "weight": w})

    # rolls：附件池总数与配置区间
    n = len(plan.entries)
    rolls = opt.attachment_rolls
    if rolls > n:
        rolls = n
    if rolls < opt.min_rolls:
        rolls = opt.min_rolls
    if rolls > opt.max_rolls:
        rolls = opt.max_rolls
    if rolls < 1 and n > 0:
        rolls = 1
    plan.rolls = rolls
    return plan


def build_all_plans(packs: Dict[str, PackModel], cfg: ScanConfig,
                    opt: CompatOptions) -> Dict[str, LootAttachmentPlan]:
    out: Dict[str, LootAttachmentPlan] = {}
    for ns, pm in packs.items():
        for g in pm.guns.values():
            out[g.full_id] = build_attachment_plan(g, pm, packs, cfg, opt)
    return out