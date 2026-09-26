---

# TACZ 1.1.4 枪战利品生成器

适用：把枪包（文件夹/zip/游戏 tacz 子包）自动生成原版 `loot_table` 的战利品表。输出枪用 `tacz:modern_kinetic_gun{GunId,GunFireMode}`，附件用 `tacz:attachment{AttachmentId}`，弹药用 `tacz:ammo{AmmoId}`。默认**不预填弹**（只设枪型与开火模式，不写弹量）。

## 1. 运行环境
- Windows 为主，Python 3.10+，装好 tkinter（标准库一般自带）。
- 依赖同目录文件：`models.py / tacz_io.py / tacz_scan.py / tacz_link.py / tacz_compat.py / tacz_ammo.py / tacz_loot.py / main_gui.py`。
- 启动：`python main_gui.py`。界面三个页：源管理 / 扫描与链接 / 战利品生成，底部日志。

## 2. 枪包放法（很重要）
工具按 1.1.4 标准数据包识别，推荐结构：
```
任意包根/
└─ data/<命名空间>/
   ├─ index/guns|attachments|ammo/*.json        # 索引/定义
   ├─ data/guns|attachments|ammo/*_data.json     # 属性数据
   └─ tacz_tags/attachments/
      ├─ allow_attachments/<枪名>.json           # 该枪允许配件，可写id或#tag
      ├─ *.json                                   # 通用配件tag（scope_sight等）
      ├─ adapter/*.json                           # 槽位适配器（scope增高座等）
      └─ ammo_mod*.json                           # 弹药修饰tag
```
兼容老路径 `data/<ns>/tags/attachments/...`。allow 文件可写：
- 直接 id：`["tacz:sro_dot","mypack:aa12_muzzle"]`
- 引用 tag：`["#tacz:scope_sight","#mypack:reddots"]`
- 裸数组或 `{"values":[...]}` 都支持，tag 里还可再写 `#其他tag`（递归展开）。

> 游戏里装枪包本身通常放 `.minecraft/tacz/*.zip`（1.1.4 无需解压），但本工具“添加文件夹”可直接选解压后的包根，“添加 Zip”可选压缩包，“添加游戏根”会自动在 tacz 子目录里发现各包。

## 3. 第一步：源管理
- 添加文件夹：选已解压枪包根（根下能看到 `data/...`）。
- 添加 Zip：选 `.zip` 枪包。
- 添加游戏根：选 `.minecraft` 或某版本根，工具进 `tacz/` 找子包；找不到时再用 `gunpack.meta.json` 兜底。
- 删除选中 / 清空：维护源列表。
- 加完点“扫描与链接”页的「扫描并链接所有源」。

## 4. 第二步：扫描与链接选项
扫描后“已发现包”列表显示 `命名空间 枪x/附件x/弹药x`。先选链接开关再扫描（或修改后重扫）：

- 严格：只按 allow 文件展开，直接 id + `#ns:tag`；不按枪型反查、不跨包补。适合作者已把允许配件写全的包。
- 启发式（默认开）：allow 为空或不完整时，按枪 data 的 `allow_attachment_types`（scope/stock/muzzle/grip/laser/extended_mag 等）反查同类型配件。 不跨包只补本包，跨包补所有已扫包。
- 跨包：allow 里裸 id/短 tag 在本包找不到时，按 base 名去其他包找；通用 tag 按同名短名去其他包回退；弹药按口径/关键字去其他包补；启发式同类型也跨包。纯枪包没弹药用它补别的包弹药最有用。
- 开火自动：枪 fire_mode=AUTO 时，附件池对扩容弹匣加权；配合“自动开火加权扩容”会把 extended_mag 优先/强制拉进池。

> 某把枪扫描后附件为 0：先看日志有没有 `未解析tag #xxx (home=yyy)`；再确认 allow 文件在 `tacz_tags/attachments/allow_attachments/<枪名>.json`，通用 tag 在 `tacz_tags/attachments/` 且名字对得上，或者关严格、开启发式补类型。

## 5. 第三步：战利品生成
### 5.1 附件池
- Rolls：每次掉落抽几个附件（默认 2）。Min/Max 限幅（默认 1/3），实际还会被“去重后总数”截断。
- 槽位过滤：留空出全部；填 `scope,muzzle,stock` 只出这些类型。类型名用 scope/muzzle/stock/grip/laser/extended_mag/misc。
- 自动开火加权扩容：AUTO 枪自动把扩容加进池并加权重；“扩容权重加成”设加多少（默认 2.0）。
- 附件权重：有 data 里 weight 的用 `weight*10` 做相对权，没有的按基础权 1；扩容再额外加。

### 5.2 弹药策略
- 跳过该枪：不出弹药。
- 不出弹药：枪照常出，弹药池空。
- 包默认弹容（默认）：用枪 data 的 `ammo_amount` / `extended_mag_ammo_amount` 给 count；没有就按弹药对象堆叠量，再没有给 8~16 兜底。
- 口径模糊：枪没写 ammo 时，按 `calibers` 和枪名关键字（9mm/556/762/12g 等）在所有已扫弹药里打分选最优；跨包开则包含其他包。
- 纯枪包自己没弹药又想出弹：开“跨包”，策略用“包默认”或“口径模糊”。

### 5.3 输出
- 输出目录：本地空文件夹或数据包根都行。
- 输出命名空间：合并总表/单独总池写这个 ns，默认 `tacz_loot`。
- 合并总表：所有勾选包所有枪合一个 `all_guns.json`（附件按权重去重合池，弹药去重合池）。
- 按原 ns 分表：不合并时每把枪写 `data/<原ns>/loot_table/tacz_loot/<gun_id>.json`；合并总表关闭才按包分文件。
- 枪掉落概率 0~1：小于 1 给枪池加 `random_chance` 条件，比如 0.5 等于宝箱一半概率出枪。

### 5.4 无枪包单独出池
- 单独出配件池：即使包里没枪，也按全部附件写池；合并总表写 `attachments_all.json`，分表写各包 `attachments.json`。
- 单独出弹药池：同理写 `ammo.json` / `ammo_all.json`。
- 纯附件包、纯弹药包用这个最方便，不必非要有枪。

## 6. 典型流程举例
1）只做一个枪包、allow 写全：
- 源管理加文件夹 → 扫描（严格可开可关，启发式开）→ 战利品页设 rolls=2、槽位空、弹药“包默认”→ 不合并、按原 ns → 生成。输出每把枪一个 json，含枪+附件池+弹药。

2）多个包混出、互相借配件/弹药：
- 全部源加入 → 扫描时开“跨包”、开“启发式”、严格关 → 战利品页勾“合并总表”+输出命名空间 `server_loot` → 生成后把输出目录当数据包放进世界 `datapacks/`。

3）只做附件盲盒箱：
- 加所有配件包 → 扫描 → 战利品页勾“单独出配件池”+“合并总表” → 生成拿 `attachments_all.json`，挂到自定义箱子 loot 里。

## 7. 输出文件位置
不合并、分原 ns：
```
输出根/data/<ns>/loot_table/tacz_loot/<gun_id>.json
```
合并总表：
```
输出根/data/<out_ns>/loot_table/tacz_loot/all_guns.json
输出根/data/<out_ns>/loot_table/tacz_loot/attachments_all.json   （仅单独配件+合并）
输出根/data/<out_ns>/loot_table/tacz_loot/ammo_all.json          （仅单独弹药+合并）
```
根目录无 `pack.mcmeta` 会自动建一个（pack_format 18，可按你的 MC 版本改）。

枪表样例结构（工具自动生成，不必手改）：
- 池1：rolls=1，entry `tacz:modern_kinetic_gun` + set_nbt `{GunId:"ns:gun",GunFireMode:"SEMI"}`
- 池2：rolls=附件数，entry `tacz:attachment` + `{AttachmentId:"ns:xxx"}`
- 池3：rolls=1，entry `tacz:ammo` + `{AmmoId:"ns:yyy"}` 和可选 set_count

## 8. 常见排错
- 扫描完成但某枪附件空：日志查“未解析tag”；检查 allow 路径、tag 文件名、是否Strict把启发式关了、是否被“槽位过滤”过滤掉。
- 跨包仍找不到：allow 写死别的 ns 但没开跨包；或各包 tag 短名不一致（A包`scope_sight`、B包`reddots`），跨包只按“同名短名”回退，不智能合并语义——这种情况在 allow 里显式写多个 `#ns:tag`。
- 纯枪包不出弹药：弹药策略选了 skip/none，或枪 data 无 ammo 且严格关但跨包没开；开跨包+口径模糊通常能补。
- 生成报参数/导入错误：确认 `tacz_loot.py` 签名含 `out_att_only/out_ammo_only`，且 `tacz_ammo.py` 有 `build_all_ammo_plans` 等。
- 适配器不出附件池：adapter tag（如 `#tacz:adapter/xxx`）走独立 resolved_adapters，不进普通 attachment 掉落池，这是按 TACZ 适配器设计隔离的。
