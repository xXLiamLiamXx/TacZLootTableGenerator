"""弹药策略：skip_gun / none / pack_default / caliber(模糊)。
返回每把枪的 ammo 掉落描述；link 后若仍空，非 strict 走 tacz_link._resolve_ammo 跨包兜底。
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from models import PackModel, GunInfo, AmmoInfo, ScanConfig

logger = logging.getLogger("tacz_ammo")

POLICIES = ("skip_gun", "none", "pack_default", "caliber")


@dataclass
class AmmoPlan:
    gun_full_id: str
    policy: str
    ammo_full_id: Optional[str] = None
    count_min: int = 0
    count_max: int = 0

    def to_loot_entry(self):
        if self.policy in ("skip_gun", "none") or not self.ammo_full_id:
            return None
        funcs = [{"function": "minecraft:set_nbt",
                  "tag": "{AmmoId:\"%s\"}" % self.ammo_full_id}]
        if self.count_max > 0:
            funcs.append({"function": "minecraft:set_count",
                          "count": {"min": self.count_min, "max": self.count_max}})
        return {
            "type": "minecraft:item",
            "name": "tacz:ammo",
            "functions": funcs
        }


def _find_ammo(pm: PackModel, packs: Dict[str, PackModel], ammo_id: str, cross: bool) -> Optional[AmmoInfo]:
    if not ammo_id:
        return None
    a = pm.ammos.get(ammo_id)
    if a:
        return a
    if cross:
        for p in packs.values():
            if p is pm:
                continue
            a = p.ammos.get(ammo_id)
            if a:
                return a
    # 按 base id 兜底（跨包时更有用）
    _ns, _, base = ammo_id.partition(":")
    if base:
        cand = list(packs.values()) if cross else [pm]
        for p in cand:
            for fid, a0 in p.ammos.items():
                if a0.ammo_id == base or fid == base:
                    return a0
    return None


def _count_for_gun(g: GunInfo, ammo: Optional[AmmoInfo], policy: str) -> (int, int):
    base = g.ammo_amount or 0
    ext = list(g.extended_mag_amount or [])
    if policy == "pack_default":
        if ext:
            hi = max(ext)
            return max(1, base or hi), hi
        if base:
            return base, max(base, int(base * 1.5))
    if base:
        return base, max(base, int(base * 1.5))
    if ext:
        hi = max(ext)
        return max(1, hi // 2), hi
    # 弹药对象若带堆叠/默认量可扩展
    if ammo:
        raw = ammo.data_raw or ammo.index_raw or {}
        for k in ("stack_size", "max_stack_size", "amount"):
            v = raw.get(k)
            if isinstance(v, int) and v > 0:
                return max(1, v // 2), v
    return 8, 16


def _resolve_via_link(g: GunInfo, pm: PackModel, packs: Dict[str, PackModel], cfg: ScanConfig) -> Optional[str]:
    if cfg.strict:
        return g.ammo_full_id or None
    try:
        from tacz_link import _resolve_ammo
        _resolve_ammo(g, pm, cfg, packs)
    except Exception as e:  # 兜底不崩
        logger.debug("ammo link resolve failed: %s", e)
    return g.ammo_full_id or None


def plan_ammo(g: GunInfo, pm: PackModel, packs: Dict[str, PackModel],
              cfg: ScanConfig, policy: str) -> AmmoPlan:
    ap = AmmoPlan(gun_full_id=g.full_id, policy=policy)
    if policy in ("skip_gun", "none"):
        return ap

    ammo_id = g.ammo_full_id
    if not ammo_id and not cfg.strict:
        ammo_id = _resolve_via_link(g, pm, packs, cfg)
    if not ammo_id and policy == "caliber" and not cfg.strict:
        ammo_id = _resolve_via_link(g, pm, packs, cfg)
    if not ammo_id:
        return ap

    ap.ammo_full_id = ammo_id
    ammo_obj = _find_ammo(pm, packs, ammo_id, cfg.cross_pack)
    lo, hi = _count_for_gun(g, ammo_obj, policy)
    ap.count_min, ap.count_max = lo, hi
    return ap


def build_all_ammo_plans(packs: Dict[str, PackModel], cfg: ScanConfig,
                         policy: str) -> Dict[str, AmmoPlan]:
    out: Dict[str, AmmoPlan] = {}
    for ns, pm in packs.items():
        for g in pm.guns.values():
            out[g.full_id] = plan_ammo(g, pm, packs, cfg, policy)
    return out