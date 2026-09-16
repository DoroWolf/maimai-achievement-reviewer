# maimai-achievement-reviewer

（所有内容皆为AI生成，仅供参考）

基于[水鱼（diving-fish）API](https://www.diving-fish.com/) 的 maimai 成绩合法性校验器：
逐条判断成绩「是否真的打得出来」，并给出「还差多少就能解」的诊断。

## 它做什么

对每条成绩，用与水鱼 / akari-bot 同源的分数模型算出**该物量下所有可达成绩**，再判断这条成绩是否落在其中：

```
总权重 T = tap + 2*hold + 3*slide + touch + 5*brk       （touch 仅 DX 谱面存在）
u        = 1000000 / T                                   （一个基础单位 = 0.0001%）
基础分    = Σ note 权重 × u × 判定系数                    PERFECT 1.0 / GREAT 0.8 / GOOD 0.5 / MISS 0
BREAK 池  = 10000 × (Σ该 BREAK 的池份额) / (20 × brk)      T1 表：CP 20 / PERFECT 15 / GREAT 8 / GOOD 6 / MISS 0
理论最高分 = 100% 基础分 + 100% BREAK 池 = 101.0000%
```

判定结果分四类（**内部标识一律英文**：`ok` / `marginal` / `impossible` / `skipped`，
**最终输出统一为中文**：终端 / JSON / CSV / 文本清单里只出现下表的中文状态）：

| 状态 | 内部标识 | 含义 |
| --- | --- | --- |
| `通过` | `ok` | 分数可达 |
| `边缘` | `marginal` | 原地无解，但**物量 ±`--tolerance`（默认 1 个）或分数差 ≤ `--score-tolerance`（默认 1 = 0.0001%）**内可解——通常是水鱼物量数据或模型舍入造成的，不应直接当作可疑 |
| `可疑` | `impossible` | 分数在物量容差与分数容差内都解不出来（`--strict` 下即「原地无解」），或超过 101.0000% |
| `跳过` | `skipped` | 宴谱（id ≥ 100000，可用 `--include-utage` 打开）或缺少谱面数据 |

本程序**只判「分数解不解得出来」，不校验成绩自带的字段**：`ra` / `rate` / `ds` / `dxScore` / `fc`
一律原样引用水鱼返回的数据，不做一致性检查。

退出码：`0` 无异常；`1` 取数失败；`2` 存在「可疑」。

## 快速开始

```bash
# 依赖管理用 uv（Python >= 3.12）
uv sync

# 1) 本地成绩文件（无需任何凭据，也可用于离线回归）
uv run python main.py --source local --records-file records.json

# 2) 公开 B50 查询（需要水鱼用户名或 QQ）
uv run python main.py --source b50 --username <水鱼用户名>

# 3) 自己的全量成绩（OAuth）
uv run python main.py --source oauth

# 3a) 首次授权：设备码流程（打开打印出的链接点「授权」，凭据写入 config.json）
uv run python main.py --login-only

# 3b) 出报告：JSON + 可疑/边缘文本清单 + CSV 表格（可直接用 Excel 打开）
uv run python main.py --source oauth --raw --csv --output
#    等价于 --raw out/report.json --csv out/report.csv --output out/suspicious.txt
#    只写文件名（如 --csv report.csv）时统一落在 out/，带目录的路径原样使用
```

OAuth 凭据只从项目根目录的 `config.json` 里读（已 gitignore，可用 `--config` 指向别的文件），
里面的键**都是可选的**，给哪个用哪个：

```json
{
  "client_id": "your-client-id",
  "client_secret": "your-client-secret"
}
```

| 键 | 谁写 | 说明 |
| --- | --- | --- |
| `client_id` | 手写可选 | 覆盖 `maimai_check/sources.py` 里写死的 `OFFICIAL_CLIENT_ID`——**公开客户端也可以用自己的**，不必非得用写死的官方值；换票请求会带上它，所以要与注册的应用一致 |
| `client_secret` | 手写可选 | 只有「机密客户端」才需要；有它时续期走 `on-behalf-of` 换票，否则走 `refresh_token` |
| `refresh_token` / `subject` | 登录后自动写入 | `--login-only` 完成设备码授权后由工具落盘；刷新会轮换令牌，新令牌会被立刻写回配置文件 |

命令行不接受 `client_id` / `client_secret`（避免泄漏到进程列表与 shell 历史），也不读任何环境变量。
登录流程是「保留已有键、同名键用新值覆盖」，所以手写的 `client_id` / `client_secret` 不会被清掉；
反过来，换了 `client_id` 就等于换了应用，旧的 `refresh_token` 会失效，需要重新 `--login-only`。

## 常用参数

| 参数 | 说明 |
| --- | --- |
| `--source {oauth,b50,local}` | 成绩来源，默认 `oauth`（`local` 读 `--records-file`） |
| `--records-file` / `--music-data-file` | 离线校验：本地成绩 / 谱面 JSON（`--source local` / 完全离线时使用） |
| `--break-table {T1,break2600,break2550,break2500}` | BREAK 判定系数表，默认 `T1`（本次数据回归的最佳拟合） |
| `--window {floor,round,union}` | 取整窗口，默认 `floor`（显示值 = 向下取整） |
| `--tolerance N` | 物量容差（个），默认 1 |
| `--score-tolerance N` | 分数容差（S 单位：1 = 0.0001%），默认 1，差值更大即记为「可疑」 |
| `--strict` | 等价于 `--tolerance 0 --score-tolerance 0`（只认原地可解） |
| `--include-utage` | 同时校验宴谱 |
| `--limit N` / `--quiet` / `--refresh` | 只校验前 N 条 / 只输出汇总 / 忽略缓存 |
| `--cache-dir DIR`（默认 `cache`）、`--config FILE`（默认 `config.json`） | 缓存与凭据位置 |
| `--raw-dir DIR`（默认 `out`） | 输出目录：`--raw` / `--csv` / `--output` 只写文件名时落在这里，带目录的路径原样使用 |
| `--raw [report.json]` | 写出完整 JSON 报告（`meta` / `summary` / `results`）；只给 `--raw` 即 `out/report.json` |
| `--csv [report.csv]` | 写出 CSV 表格（UTF-8 BOM + CRLF，Excel 双击即开）；只给 `--csv` 即 `out/report.csv` |
| `--output [suspicious.txt]` | 把「可疑 + 边缘」清单写成文本档案（曲名 / ID / 类型 / 难度 / 等级 / 定数 / 成绩 / 全连 / 连锁 / 物量 / 说明）；只给 `--output` 即 `out/suspicious.txt` |
| `--login-only` | 只完成 OAuth 设备码授权并把凭据写入 `config.json`，不拉取成绩 |

取整窗口的含义（`R` 为真实成绩、`S` 为显示值，单位都是 0.0001%）：

```
floor：S = floor(R)   → 2R ∈ [2S,     2S + 2)
round：S = round(R)   → 2R ∈ [2S - 1, 2S + 1)
union：两者取并（最宽松）
```

## 输出

1. **终端汇总**：一行统计 + 判定配置，`--quiet` 只保留汇总。
2. **JSON 报告**（`--raw`）：`meta`（来源、判定表、窗口、容差、生成时间）/ `summary`（键为中文状态名）/ `results`（每条含 `status`（中文）/ `notes` / `ra` / `rate` / `fc` / `fs` / `nearest_delta` 等；`成绩(%)` 已能唯一确定分数，故不再重复输出「分数(S)」）。
3. **文本清单**（`--output`）：把所有非「通过」的成绩（可疑 → 边缘）写成 UTF-8 等宽文本档案，
   便于存档、对比与人工复查。文件结构：

   ```
   maimai 成绩合法性校验 —— 可疑/边缘成绩清单
   ------------------------------------------------------------------------
   生成时间：2026-09-16 20:56:16
   数据来源：本地文件 cache\records.json（来源 local）
   判定配置：T1 表 / floor 窗口 / 物量容差 1 / 分数容差 0.0001%
   统计：共校验 3906 条成绩 | 可疑 851 | 边缘 326 | 跳过 0 | 通过 2729
   说明：可疑 = 该物量下无法达成；边缘 = ...；跳过 = ...

   【可疑】851 条
   曲名  ID  类型  难度  等级  定数  成绩  全连  连锁  物量  说明
   ...
   ```

   列含义：`曲名` / `ID`（水鱼 `song_id`）/ `类型`（`SD` / `DX`）/ `难度`（`BSC`…`ReM`）/ `等级`（如 `13+`）/
   `定数` / `成绩`（4 位小数百分数）/ `全连`（`fc` 字段：`FC` `FC+` `AP` `AP+`，缺失记 `-`）/
   `连锁`（`fs` 字段：`FS` `FS+` `FSD` `FSD+` `SYNC`）/ `物量`（`tap+hold+slide+touch+brk`）/ `说明`（判定原因）。
   排序：可疑组按「与最近可行成绩的差值」降序（越离谱越靠前），其余组按 `(ID, 类型, 难度)` 升序，便于 `diff`。
4. **CSV 表格**（`--csv`）：每条成绩一行（含「通过」「跳过」），可直接用 Excel / WPS 打开或另存为 xlsx。
   * 编码 `UTF-8 with BOM` + `CRLF` 换行：双击即开，中文不乱码；
   * **不按状态分组**（可疑与边缘一视同仁），统一按 `(ID, 类型, 难度)` 升序，便于 `diff`；状态仍保留在 `状态` 列；
   * 列：`状态` / `曲名` / `ID` / `类型` / `难度` / `等级` / `定数` / `成绩(%)` / `RA` / `评级` /
     `全连` / `连锁` / `物量`（`tap+hold+slide+touch+brk`）/ `总物量` / `最近可行差值(%)` / `说明`；
   * `成绩(%)`、`总物量`、`最近可行差值(%)` 都是裸数字（如 `98.4464` / `+0.0058`），可直接排序与透视；
     与 `成绩(%)` 重复的「分数(S)」列已去掉（`成绩(%) × 10000` 才是它）。

   所有输出路径都会在终端打印**绝对路径**；`--raw` / `--csv` / `--output` 只写文件名时统一落在
   `--raw-dir`（默认 `out/`），带目录的写法（`out/x.csv`、`D:\tmp\x.csv`）原样使用。

## 算法与性能

朴素做法是枚举所有 BREAK 的 `(基础分合计, 池份额合计)` 组合，再枚举非 BREAK 部分的贡献，复杂度约
`O(brk³)`，`brk` 上百时完全不可用。现在的实现：

1. **非 BREAK 部分用位集合**：`non_break_bitmask` 用一个整数的二进制位表示「可达的基础分合计」，
   `tap + 2*hold + 3*slide` 个单位通过移位累加（`mask | mask << w`），每种物量只算一次（`lru_cache`）。
2. **BREAK 部分按池份额建位集合**：`break_share_bases(brk, table)` 为每个可达的 BREAK 池份额记录
   「能得到该份额的基础分合计」位集合，按 `brk` 增量构造并缓存（先算 `brk` 再算 `brk+1` 只需一轮移位）。
3. **O(1) 份额级查表**：`break_lookup` 把「份额 ≥ x」的可行基础分并成位集合，并按位翻转
   （`_bit_reverse`，逐字节查表）得到倒序编码，于是判定只需一次移位 + 掩码。
4. **每个 `(brk, table)` 只建一次**：判定一条成绩只遍历可达的 BREAK 池份额（`≤ 20*brk + 1` 个），
   而不是全部组合。

`brk > 64`（`EXACT_BRK_LIMIT`）时组合数仍会爆炸（`brk = 64` 已约 8 万个组合，而水鱼数据中最大 `brk` 可达 491），
此时退化为**保守判定**：只用两个宽松条件——「每 1 点池份额最多对应 5 点、最少对应 2.5 点基础分」，
以及「非 BREAK 部分的贡献落在其可达区间内」。保守判定的结果是真可达集合的**超集**：

* 不会把正常成绩误判为「可疑」（无假阳性）；
* 大 `brk` 谱面上的可疑成绩可能被漏报（明细中表现为「通过」）。

实测：水鱼公开测试数据（`/player/test_data`）的 1434 条成绩端到端约 **3.9 秒**（含取数与 JSON 输出），
默认容差下为 2 可疑 / 1 边缘 / 1431 通过（三条「差一点」记录：0.0004%、0.0058% 与物量 ±1）。
真实账号（3906 条）约 **16 秒**（`--source local` 读缓存的成绩文件，含 JSON + CSV + 文本清单输出）。


## 已知限制

* 模型假定所有音符判定相互独立，不考虑实际打法（转圈、Touch 加成等），所以给出的是「理论可达集合」；
  判为「可疑」意味着**按官方计分公式无论怎么打都得不到这个分数**。
* 分数容差（默认 1 = 0.0001%）与物量容差（默认 1 个）用于容忍水鱼物量数据与实机版本的差异；
  需要「最后一位也不许差」的结论时用 `--strict`，需要更宽松时可自行调大这两个参数。
* 默认容差很紧（物量 ±1 / 分数 ±0.0001%）：真实数据里约 8%（326/3906）的记录会落进「边缘」，属于实机取整的正常范围；
  差值越小越可能是数据舍入，差值越大越可能是伪造。需要「一位小数都不许差」时用 `--strict`。
* Windows 控制台默认 GBK，曲名里的半角片假名（如 `ﾟ`）无法编码，输出时会以 `?` 代替而不是中断报告
  （`--output` / `--raw` 落盘的文件始终是完整 UTF-8）。
* 水鱼 `player/test_data` 是被随机化过的公开测试数据（`dx` / `fc` 等字段不可信）；
  本程序不再提供该在线来源，需要离线回归时把数据存成本地 JSON 再用 `--source local` 读取。
* 宴谱（`id ≥ 100000`）物量口径与通常谱面不同，默认跳过。

## 目录结构

```
main.py          CLI 入口（取数 → 逐条校验 → 汇总 / JSON）
maimai_check/
  scoreline.py           分数可行域模型（纯逻辑，无网络）
  checks.py              单条成绩校验：可解性判定与状态定义（内部英文标识 + 中文输出标签）
  sources.py             水鱼 API 客户端：OAuth（client_id 可配、缺省用写死的官方值）、缓存、music_data / records / b50
  report.py              终端表格（CJK 宽度对齐）与 JSON 报告
```
