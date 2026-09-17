# maimaidx-achievement-reviewer

基于水鱼（diving-fish）API 数据的 maimai DX 成绩合法性检查工具。

它会根据谱面物量和官方计分规则，判断一条成绩是否理论可达，并标记可能存在异常的数据。

本程序提供的结果仅供参考，不可作为出警依据。

## 功能
* 检查成绩是否符合计分规则
* 标记理论上无法达成的成绩
* 识别接近边界、可能受数据误差影响的成绩
* 支持公开 B50、OAuth 全量成绩和本地成绩文件
* 可导出 JSON、CSV 和文本报告

## 判定结果

| 状态 | 说明                          |
| -- | --------------------------- |
| 通过 | 成绩符合计分规则                    |
| 边缘 | 成绩与可达范围非常接近，可能受到物量数据或取整误差影响 |
| 可疑 | 按当前模型无法推导出该成绩               |
| 跳过 | 宴谱或缺少谱面数据             |

本工具仅检查成绩是否可正常达成，不会校验 Rating、DX Score、FC 等其他字段。

## 快速开始

安装依赖：

```bash
uv sync
```

检查本地成绩文件：

```bash
uv run python reviewer.py --source local --records-file records.json
```

检查公开 B50：

```bash
uv run python reviewer.py --source b50 --username <用户名>
```

检查自己的完整成绩：

```bash
uv run python reviewer.py --source oauth
```

首次使用 OAuth：

```bash
uv run python reviewer.py --login-only
```

生成完整报告：

```bash
uv run python reviewer.py --source oauth --raw --csv --output
```

## 常用参数

| 参数                           | 说明           |
| ---------------------------- | ------------ |
| `--source {oauth,b50,local}` | 成绩来源         |
| `--records-file`             | 本地成绩文件       |
| `--strict`                   | 严格模式，不使用任何容差 |
| `--include-utage`            | 同时检查宴谱       |
| `--limit N`                  | 仅检查前 N 条成绩   |
| `--quiet`                    | 仅输出汇总结果      |
| `--refresh`                  | 忽略缓存重新获取数据   |
| `--raw`                      | 导出 JSON 报告   |
| `--csv`                      | 导出 CSV 报告    |
| `--output`                   | 导出文本报告       |
| `--login-only`               | 仅完成 OAuth 登录 |

运行 `--help` 可查看完整参数说明。

## 输出文件

### JSON 报告

包含完整检查结果和统计信息，适合进一步处理或分析。

### CSV 报告

每条成绩对应一行，可直接使用 Excel 打开。

### 文本报告

列出所有“可疑”和“边缘”成绩，方便人工复查。

## 已知限制
* 结果仅基于理论计分模型，不考虑实际游玩中的情况。
* 默认会保留少量容差，以兼容不同版本数据和取整差异。
* 宴谱由于计分方式可能不同，默认不参与检查。
* 缺少谱面数据时，对应成绩会被直接跳过。

## 数据来源
成绩与谱面数据来自 [diving-fish](https://www.diving-fish.com/maimaidx/prober/)。
