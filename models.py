from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class AttachmentInfo:
    attachment_id: str = ""
    full_id: str = ""
    ns: str = ""
    type: str = ""          # scope/muzzle/stock/grip/laser/extended_mag/misc
    weight: float = 1.0
    sort: float = 0.0
    data_ref: str = ""
    index_raw: dict = field(default_factory=dict)
    data_raw: dict = field(default_factory=dict)


@dataclass
class AmmoInfo:
    ammo_id: str = ""
    full_id: str = ""
    ns: str = ""
    caliber: str = ""
    type: str = ""
    data_ref: str = ""
    display_name: str = ""
    index_raw: dict = field(default_factory=dict)
    data_raw: dict = field(default_factory=dict)


@dataclass
class GunInfo:
    gun_id: str = ""
    full_id: str = ""
    ns: str = ""
    gun_type: str = ""
    fire_mode: str = "SEMI"
    data_ref: str = ""
    display_ref: str = ""
    # allow原始（可含 #tag / 直接id）
    allow_list: List[str] = field(default_factory=list)
    # gun data
    allow_attachment_types: List[str] = field(default_factory=list)
    exclusive: Dict[str, dict] = field(default_factory=dict)
    ammo_full_id: str = ""
    ammo_amount: int = 0
    extended_mag_amount: List[int] = field(default_factory=list)
    calibers: List[str] = field(default_factory=list)
    # link结果
    resolved_attachments: List[str] = field(default_factory=list)
    resolved_adapters: List[str] = field(default_factory=list)
    resolved_ammo_mods: List[str] = field(default_factory=list)
    index_raw: dict = field(default_factory=dict)
    data_raw: dict = field(default_factory=dict)


@dataclass
class PackModel:
    ns: str = ""
    source_label: str = ""
    guns: Dict[str, GunInfo] = field(default_factory=dict)
    attachments: Dict[str, AttachmentInfo] = field(default_factory=dict)
    ammos: Dict[str, AmmoInfo] = field(default_factory=dict)
    # 原始索引缓存（scan内部用）
    _raw_index_gun: Dict[str, dict] = field(default_factory=dict)
    _raw_index_att: Dict[str, dict] = field(default_factory=dict)
    _raw_index_ammo: Dict[str, dict] = field(default_factory=dict)
    _raw_data_gun: Dict[str, dict] = field(default_factory=dict)
    _raw_data_att: Dict[str, dict] = field(default_factory=dict)
    _raw_data_ammo: Dict[str, dict] = field(default_factory=dict)
    _raw_allow: Dict[str, list] = field(default_factory=dict)
    _raw_slot_tags: Dict[str, list] = field(default_factory=dict)     # name / ns:name -> 原始条目
    _raw_adapter_tags: Dict[str, list] = field(default_factory=dict)  # name -> 原始条目
    _raw_ammo_tags: Dict[str, list] = field(default_factory=dict)      # ammo_mod tag

    def stats(self) -> str:
        return f"枪{len(self.guns)}/附件{len(self.attachments)}/弹药{len(self.ammos)}"


@dataclass
class ScanConfig:
    strict: bool = False
    heuristic: bool = True
    cross_pack: bool = False
    auto_fire: bool = True