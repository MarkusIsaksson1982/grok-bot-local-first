# W3 - AI support for test design and human review (90 min)

**Goal:** a team checklist for AI-generated tests and code, and one automated check that runs in CI.

- Test ideas from acceptance criteria: boundary values, error paths, permissions. The assistant proposes; testers prune.
- Generated unit tests: run them, then break the code on purpose - a test that still passes is not a test.
- Review checklist for AI-written diffs: does it compile and pass, does it touch files it should not, are new dependencies justified,
  are there invented APIs, is there a test for the changed behaviour.
- Deterministic gates: schemas for structured model output, a regression gate on an evaluation set, link and number checks in docs.
- Wrap-up: the checklist goes into the shared library; the team picks one gate to add to CI this sprint.
