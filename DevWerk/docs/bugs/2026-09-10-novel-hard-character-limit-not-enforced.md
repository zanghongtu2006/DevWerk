# Novel hard character limit is not enforced at delivery

- Date: 2026-09-10
- Severity: P1
- Status: Deferred — user requested prioritizing the earlier scope-extension bugs
- Project: `prj_22d5638974ee405885b0552e4a24e91f`
- Task: `tsk_93c8b7f37cd34e44b0332e94986be4ab` (`chapter01`)
- Loop: `novel.production` v1.4.0

## 2026-09-10 combined review

已与 [范围扩展问题](2026-09-10-conversation-scope-extension-review-and-plan.md) 统一评估，具体实施安排见 [统一修复方案](../DEVWERK_Novel_Combined_Remediation_Plan_2026-09-10.md)。保留本 Bug 的独立关闭条件；原 P1 登记保留，按确定性硬条件失效的 V1 发布阻塞口径，建议纳入 P0 修复批次。

本次只读复核再次实测 chapter01 为4142个非空白字符，DB 仍为 done，review 同时写4142和 accepted。该 Task 绑定的 Workflow 各 Column 的 acceptance_checks 全部为空。平台具备固定检查机制不代表这个 Loop 已接入检查。

计划增加固定阈值的确定性校验、同一产物版本的计数/哈希/审查关联，以及声明式返工路径。扩写范围修订与字数校验共享 scope/binding 快照；修改章节数量不自动改变4000字硬上限。尚未实施代码或修改历史正文。

## Expected

The Project Loop binding declares `chapter_max_characters=4000`. The Loop describes this value as a hard upper bound, so a chapter above 4000 non-whitespace characters must not reach `done`.

## Actual

The final `chapters/chapter01.md` contains 4142 non-whitespace characters. The second review report records the same count but states that it conforms, then emits `accepted`. The `deliver` Column succeeds and the Task reaches `done`.

Evidence:

- `reviews/chapter01_review.md`: `正文非空白字符数: 4142`
- Review conclusion: `accepted`
- Final Task path: `foundation -> recap -> authoring -> review -> authoring -> review -> deliver -> done`
- Project binding: `chapter_max_characters: 4000`

## Impact

A declared hard Project constraint currently depends on an LLM interpreting its own measurement. The Workflow can therefore accept and deliver an objectively invalid artifact. This affects deterministic acceptance semantics, not only novel quality.

## Suspected boundary

The character limit is present in Loop bindings and prompts, but no deterministic acceptance check rejects a measured value above the bound before `review` or `deliver` completes.

## Reproduction outline

1. Apply `novel.production` v1.4.0 with `chapter_max_characters=4000`.
2. Run a chapter through authoring and review.
3. Produce a body whose measured non-whitespace character count exceeds 4000.
4. Observe that an LLM `accepted` outcome can still transition through `deliver` to `done`.
