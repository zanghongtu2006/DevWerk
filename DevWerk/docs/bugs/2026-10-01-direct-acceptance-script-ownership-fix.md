# P0：直接执行验收脚本绕过 Column 脚本归属校验

第九次黑盒 Project `prj_4859dd2f5bd74077a313f2b109258fb4` 在授权后创建单 Task，冻结 Workflow 的 `backend_development`、`frontend_development` 分别执行 `scripts/run-backend-tests.bat`、`scripts/run-frontend-tests.bat`。复查授权 Run 的工具记录后确认：这两个文件在发布 Workflow 前已由 Conversation Agent 创建。因此本次实际障碍不是“脚本不存在”，而是后端 Column 无权修正已有脚本，且其验收执行未生成合格的新报告。下面的直接脚本识别缺口是在复查时发现的独立准入缺陷，不应记作本次失败的直接根因。

根因是 `app/v1/capabilities.py::_behavior_runner_script_index` 只识别 `python script.py`、`cmd /c script.bat` 等解释器入口，没有识别 `scripts/verify.bat` 这样的项目相对可执行脚本。共享所有权校验因此跳过它，发布入口一致化也不能阻挡。

最小修改：只对带路径分隔符、扩展名为 `.py/.js/.mjs/.cjs/.ps1/.sh/.cmd/.bat` 的直接可执行脚本返回索引 0；继续用现有已存在/本列可写/更早 Column 同脚本承诺规则校验。裸 `npm.cmd` 等 PATH 工具不作为项目脚本处理。补充高级配置与简化 runner 的回归：未拥有的 `scripts/*.bat` 被拒，`backend/*.bat` 可通过，已存在脚本可复用。其他行为命令语义保持不变，单项提交。
