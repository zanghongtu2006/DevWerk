# 小说 Project 各章节、各 Column 的 LLM 输入规模审计

Project：`prj_b7e19c05f54f4177bd330037ea68daa4`。本次只读 `data/logs/devwerk.log` 和 `data/devwerk.db`，未调用模型、未修改 Workflow 或服务。日志中该 Project 的 Column Agent 请求从 2026-10-23 16:52:52 至 21:19:28，共 429 次；第 1–5 章已到 `done`，第 6 章当时停在 `review/recovering`，因此没有第 6 章 `deliver` 数据。全部逐次指标在 `.tmp/novel-request-sizes.csv`，不包含提示词、正文或密钥。

## 统计口径

通过请求的 system envelope 中 `agent.kind=column` 和 `agent.project_id` 筛选，再由 Task/Column Run 的数据库记录确定章节与 Column。每一条 `llm.provider_request` 均计算完整 payload 的未转义 UTF-8 JSON 字节数；“首次”指该章节首次进入该 Column 的首个模型请求，之后的返工及工具循环计入“全部请求”。CSV 另有用当前 `requests` JSON 序列化方式计算的 HTTP body 字节数。KB 为十进制 1,000 字节，**不是 token**。429 条均找到了匹配的 Assignment frame 与 Column Run；6 条对应日志中的 `LLM_TIMEOUT`。

首次进入每列的请求（KB）：

| 章节 | foundation | recap | authoring | review | deliver |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 28.6 | 63.2 | 32.8 | 42.7 | 48.7 |
| 2 | 68.1 | 76.3 | 33.4 | 86.4 | 108.9 |
| 3 | 73.3 | 61.2 | 36.6 | 101.1 | 136.8 |
| 4 | 78.7 | 101.2 | 38.5 | 114.0 | 167.2 |
| 5 | 93.8 | 118.2 | 42.5 | 140.0 | 217.6 |
| 6 | 109.8 | 132.0 | 45.7 | 162.4 | 无请求 |

逐章节/列的全部请求分布；“次数”是模型请求数，“尝试”是不同 Column Attempt 数：

| 章节 | Column | 次数 | 尝试 | 最小 / 中位 / 最大 KB | 超时 |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | foundation | 15 | 1 | 28.6 / 95.6 / 150.7 | 0 |
| 1 | recap | 2 | 1 | 63.2 / 65.0 / 66.9 | 0 |
| 1 | authoring | 11 | 3 | 32.8 / 54.6 / 74.4 | 0 |
| 1 | review | 20 | 3 | 42.7 / 77.7 / 107.1 | 0 |
| 1 | deliver | 2 | 1 | 48.7 / 50.4 / 52.1 | 0 |
| 2 | foundation | 6 | 1 | 68.1 / 87.4 / 124.9 | 0 |
| 2 | recap | 2 | 1 | 76.3 / 78.7 / 81.1 | 0 |
| 2 | authoring | 30 | 4 | 33.4 / 68.7 / 129.1 | 0 |
| 2 | review | 29 | 5 | 43.2 / 109.4 / 168.5 | 1 |
| 2 | deliver | 2 | 1 | 108.9 / 110.6 / 112.3 | 0 |
| 3 | foundation | 17 | 1 | 73.3 / 155.5 / 214.6 | 0 |
| 3 | recap | 6 | 3 | 61.2 / 65.1 / 68.4 | 0 |
| 3 | authoring | 15 | 4 | 36.6 / 60.2 / 72.0 | 0 |
| 3 | review | 30 | 5 | 51.3 / 125.3 / 181.8 | 1 |
| 3 | deliver | 7 | 1 | 136.8 / 142.7 / 149.5 | 0 |
| 4 | foundation | 10 | 1 | 78.7 / 142.8 / 207.8 | 0 |
| 4 | recap | 3 | 1 | 101.2 / 110.0 / 118.7 | 0 |
| 4 | authoring | 10 | 2 | 38.5 / 60.5 / 70.8 | 0 |
| 4 | review | 14 | 2 | 114.0 / 139.0 / 212.6 | 0 |
| 4 | deliver | 2 | 1 | 167.2 / 168.9 / 170.6 | 0 |
| 5 | foundation | 28 | 2 | 93.8 / 161.5 / 246.8 | 0 |
| 5 | recap | 2 | 1 | 118.2 / 123.7 / 129.3 | 0 |
| 5 | authoring | 48 | 3 | 32.4 / 100.8 / 174.4 | 1 |
| 5 | review | 15 | 2 | 140.0 / 185.8 / 226.3 | 0 |
| 5 | deliver | 2 | 1 | 217.6 / 220.0 / 222.3 | 0 |
| 6 | foundation | 29 | 1 | 109.8 / 203.4 / 254.8 | 0 |
| 6 | recap | 2 | 1 | 132.0 / 139.4 / 146.8 | 0 |
| 6 | authoring | 59 | 6 | 31.9 / 105.7 / 183.5 | 3 |
| 6 | review | 11 | 3 | 162.4 / 191.0 / 210.4 | 0 |

## 增长从哪里来

先前抽样把第 2 章 `review`（约 105 KB）与第 5、6 章 `authoring`（约 45–48 KB）相比，属于不同 Column，不能说明第 2 章比后续章节更重。同列比较后，`review` 首次请求从第 2 章 86.4 KB 升至第 5 章 140.0 KB、第 6 章 162.4 KB。第 1→6 章的 `foundation` 首次请求增长 3.8 倍，`recap` 增长 2.1 倍，`review` 增长 3.8 倍；第 1→5 章的 `deliver` 增长 4.5 倍。只有 `authoring` 首次请求相对稳定（32.8→45.7 KB），但它在返工及工具循环中也会升至 100–183 KB。因此“各章各列输入约莫相同”不成立，增长发生在首次进入 Column 时，不能全归于返工意见。

具体来源由每个 Assignment frame 的文件路径与长度核实：

1. `foundation` 预装前一 Task 验收的六份 `baseline/*.md`。这些文件的正文总长由第 1 章完成后的 12,026 字符增至第 6 章 foundation 完成后的 30,221 字符；第 6 章 foundation 的首次请求中，前章基线一项约占 76.1 KB。基线内容自身在增长。
2. `recap` 首次请求同时带入直接前章的提要和正文，以及当前 Task foundation 写下的基线文件。第 6 章仅基线的预装 JSON 就约 87.2 KB。第 3 章是异常低点：foundation 只在当前 Task 登记了两份修订基线，recap 仅预装这两份（8,908 字符），其余四份未预装；该列的六次请求里也没有读取那四份文件的工具调用。这是上下文完整性问题，不能把小请求简单视为优化成功。
3. `authoring` 未预装前章正文，说明写作列的历史边界在这批请求中生效；但当前提要从第 1 章 775 字符持续增至第 6 章 4,649 字符（Loop 指令期望约 100 字）。`task.context` 还带入先前 foundation 等列的输出，完整 Loop 方法资产和通用规划信息也被投影。后续 100 KB 以上的请求主要包含同一 Session/Attempt 的历史消息，不等于首次装配有那么大。
4. `review` 以 transitive 依赖选择 `baseline/*.md` 与 `chapters/*.md`，因此预装不断增长的基线和**所有前章正文**，再加当前章提要、正文、已有 review。第 2 章首次 review 的 accepted artifacts 约 39.9 KB；第 6 章约 98.7 KB。历史连续性检查可能需要追溯具体文件，但默认全量预装并没有利用 recap 的压缩边界。
5. `deliver` 在非最终章也按 transitive 依赖预装前章的基线、正文、提要和 review。第 5 章首次请求的 accepted artifacts 已达约 137.5 KB、18 份文件，而 Loop 对非最终章的职责只是核对并记录当前章交付；全历史预装与该职责不相称。第 6 章尚无 deliver 请求。
6. 同一 Attempt 内还会累积模型工具调用参数及结果。最大请求是第 6 章 foundation 的 254.8 KB：它从首次 109.8 KB 增长至 25 条消息，新增消息约 145 KB，其中工具调用参数约 88 KB、工具结果约 56 KB；该 Agent 曾读取多章历史并写入六份基线。这解释了列内峰值，和前五点的首次投影增长是两种不同现象。

三个重放案例之外的另外三次历史超时分别是第 3 章 `review` 114.5 KB，以及第 6 章 `authoring` 恢复会话中的 101.2、105.7 KB。六次超时请求并非最大请求：更大的 200–255 KB 请求也有成功记录。因此输入体积是真实的上下文效率与边界问题，**不能仅凭体积认定它是 600 秒超时的唯一原因**。

## 代码边界与后续修正方向

`app/v1/domain.py` 的 `ContextSelection` 给 project、Loop assets、Task context 等选项默认值 `True`；`app/v1/execution/runtime.py::_build_input` 按 Column 选择器预装 artifact，并把每列输出保存在 Task context，另行投影 `upstream_outputs`。后者在第 2 章 review 中与 `task.context` 的三个对象完全相同，形成约 3.7 KB 可证实的重复。`loops/novel-production/loop.json` 的列选择器决定了 transitive 的 review/deliver 历史范围。最先需要核对的是各列实际需要的来源与输出边界，尤其是 foundation 基线增长、recap 的完整性、review/deliver 的全历史预装，以及提要文件持续扩张；在确定契约前不应以任意字节截断代替修正。
