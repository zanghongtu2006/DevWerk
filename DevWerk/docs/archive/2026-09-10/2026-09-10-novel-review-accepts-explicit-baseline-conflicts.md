# Novel review accepts explicit baseline conflicts

- Date: 2026-09-10
- Severity: P2
- Status: Open
- Project: `prj_197e3a92ba3e4b919fbfdb6dc18d13ea`
- Task: `tsk_7775a0a03cee412aa99be538228fc11a` (`ch01`)
- Loop: `novel.production` v1.5.0

## Expected

The review Column checks the current chapter against the stable Project baseline. When it finds concrete character or timeline conflicts, it should either reject the chapter for targeted revision or apply an explicit Loop-defined policy that explains why those conflict classes are non-blocking.

## Actual

`reviews/ch01.md` identifies two baseline conflicts but still emits `accepted`:

1. The stable character baseline says the young father is in his early thirties, while the chapter describes him as about 27 or 28.
2. The stable baseline says he comes every other Friday, while the chapter takes place on Thursday and gives no reason for the exception.

The report labels both deviations minor and non-blocking, but no declared acceptance policy distinguishes permissible approximation from a continuity failure. The Task therefore transitions directly through `deliver` to `done` with known baseline conflicts.

## Impact

The Workflow records useful review evidence, but the evidence does not reliably control the transition. Repeated acceptance of known conflicts can accumulate continuity drift across later chapters.

## Reproduction outline

1. Apply `novel.production` v1.5.0 with a stable character and schedule baseline.
2. Author a chapter containing a different age and day-of-week for the same character.
3. Let the review report identify both differences.
4. Observe that the report can still emit `accepted` and allow delivery.
