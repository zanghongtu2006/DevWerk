# 软件交付验收实例化合同（随冻结 Loop 提供给每列）

Task input.acceptance_scenarios 必须列出已确认需求中稳定的场景 ID，并在需求文件/追踪矩阵记录 ID 对应的用户行为、预期和范围。不得另加产品需求。修复允许替换测试实现，但必须保留等价场景映射，不能删除失败场景换取通过。

规划前 loop.inspect 查询 bindings schema；loop.apply 后使用 workflow.acceptance.configure，只传基线 revision/digest、各列 checks 和 obligations。task.plan.validate 集中检查所有缺失项，再保存正式计划。检查入口允许在对应实现列生成；配置不会提前运行测试。

为每个必需行为列冻结一个真正运行断言并生成报告的入口脚本，例如由后端列生成 backend/verify_acceptance.py，然后以项目根目录为 cwd 执行解释器加相对脚本路径；具体解释器和命令应根据项目环境选择。普通 mvn/npm build 不自动生成该报告，-DskipTests 不能证明后端行为，cmd /c dir 不能证明最终验收。构建可另列 artifact 检查。声明 launch_validation=preflight 可在保存计划前只检查可执行文件解析；未来生成的脚本使用 deferred，Runtime 在实际执行前检查。Windows 批处理使用 .cmd/.bat 入口，不将 Unix mvnw 当作 Windows 原生程序。

必需的行为检查须声明 scenario_ids 和 report_path（项目内的具体 JSON 文件，不能是目录）。命令必须运行断言并在本次执行中重写报告；exit code 0、curl 传输成功或报告文件存在本身均不足以证明行为通过。失败应返回非零；跳过场景不算通过。最终 accept 的检查必须覆盖全部冻结场景。每个报告使用以下结构：

```json
{
  "schema_version": "devwerk.acceptance-report.v1",
  "scenarios": [{
    "id": "requirement-scenario-id",
    "status": "passed",
    "assertions": [{"expected": 401, "actual": 401}],
    "test_source": {"path": "backend/tests/check.py", "sha256": "SHA256 of exact executed test source bytes"}
  }]
}
```

Runtime 绑定本次执行回执、当前 Task/Attempt、冻结 check、报告摘要和测试源码 SHA256，核对必需场景已执行且断言相等。旧报告、不完整/跳过场景和源码不匹配拒绝通过。报告路径需落在负责列的写入范围内，测试命令负责生成；无需模型重复抄写日志或报告。

backend/frontend 负责自身范围的测试及场景映射；integration_review 必须读取实际测试源码、核对断言对象和需求语义，比较替换/删除前后的场景映射。system_test 执行真实 HTTP/浏览器用户路径；delivery 验证启动、就绪、运行和 finally 清理；accept 核对全部必需场景并真实复验。结构化报告不能证明任意测试脚本语义正确，仍需这些已有独立列审查，不新增 Agent。

冻结 checks 的 report_path 及入口均应选用对应 Column 已允许的项目路径。报告文件只作 artifact，Runtime 读取的结构化断言才是行为验证记录。生产中既有冻结任务保持旧合同，需显式更换修订才能使用新合同。
