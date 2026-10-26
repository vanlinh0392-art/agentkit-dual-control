---
name: smart-test
description: >-
  Intelligent test engineering skill with blast-radius lane system for fast CI.
  Use when creating tests (create), auditing test quality (audit), or optimizing
  CI speed (optimize). Enforces behavior-first reasoning: never test implementation,
  never comment out tests without explaining why, never skip without permission.
  Triggers on: 'viết test', 'tạo test', 'kiểm thử', 'test coverage', 'audit test',
  'xóa test', 'tìm test rác', 'cải thiện CI', 'tăng tốc CI', 'blast radius test'.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
---

# Smart Test Engineering Skill

> Tests are not checkbox compliance. They are executable specifications of real behavior.
> Every test must earn its place. Every failure must be explained. Every skip must be justified.

---

## ENTRY: COMMAND DISPATCH

When invoked, identify the sub-command from the user's message:

| Command | Trigger | Action |
|---|---|---|
| `/test create` or `tạo test / viết test` | Missing coverage, new feature, bug fix | → Run **CREATE PROTOCOL** |
| `/test audit` or `audit test / review test / tìm test rác` | Suspect tests, pre-refactor, CI cleanup | → Run **AUDIT PROTOCOL** |
| `/test optimize` or `tăng tốc CI / optimize CI` | CI too slow, expensive runner cost | → Run **OPTIMIZE PROTOCOL** |
| `/test optimize --ultra` | Need multiple competing strategies | → Run **ULTRA OPTIMIZE PROTOCOL** |

---

## CREATE PROTOCOL: Build Coverage Around Real Behavior

### Phase 1 — Scout First (Do Not Write Yet)

Before touching any test file, answer all five questions:

```
1. What behavior actually changed?
   → Trace the diff. Name the specific function, route, state transition, or contract.

2. What can break?
   → Think about callers, downstream consumers, edge cases, race conditions, error paths.

3. What tests already prove this?
   → Grep for existing coverage. Never duplicate proof.

4. What is the lowest-trust layer that can verify this?
   → Unit > Integration > E2E (prefer lower cost, lower trust level).

5. Do we need additional tests?
   → Only if a gap exists after answering 1–4.
```

**Do not write a single test until you can answer all five.**

### Phase 2 — Test Architecture Rules

```
✅ Test behavior, not implementation.
   - WRONG: "calls saveToDatabase() with payload"
   - RIGHT:  "persists user after successful registration"

✅ Test at the right trust level.
   - Unit:        Pure functions, isolated logic, single responsibility
   - Integration: Cross-module contracts, DB writes, API responses
   - E2E:         Critical user journeys ONLY (≤10% of total test count)

✅ Each test must be independently falsifiable.
   - One reason to fail. One assertion of real consequence.

✅ Test names describe behavior, not code.
   - WRONG: test_calculateTax_line42
   - RIGHT:  "returns 0 tax for tax-exempt organizations"
```

### Phase 3 — Blast Radius Lane Assignment

Every new test MUST be tagged with its blast-radius lane:

```
# Lane taxonomy
LANE: core        → Domain logic, critical business rules (always runs in CI)
LANE: api         → HTTP routes, controllers, API contracts
LANE: db          → Migrations, queries, data integrity
LANE: ui          → Components, rendering, interactions
LANE: integration → Cross-service, queue consumers, webhooks
LANE: e2e         → Full user journeys (runs on release candidates ONLY)

# Tag format (add to test file header or describe block):
@blast-radius: core
@blast-radius: api, db
```

**Rule:** If you can't assign a lane, the test scope is too broad — split it.

### Phase 4 — Write (RED → GREEN)

- Write minimal test code that proves the behavior.
- Run it. Watch it **fail first** (RED).
- Only then implement the fix (GREEN).
- Do not refactor production code while making tests pass.

---

## AUDIT PROTOCOL: Find Tests That Lie, Hide, or Waste

### Step 1 — Scan for Problem Categories

```
❌ DEAD:      Skipped indefinitely with no ticket/issue reference.
              → grep: .skip, xit, it.todo, @Ignore with no linked issue

❌ HOLLOW:    Always passes, even when the code is broken.
              → No assertions, assertion on constants, unconditional mock returns

❌ REDUNDANT: Multiple tests proving the exact same thing.
              → Cluster by behavior. Keep one. Delete the rest.

❌ OVERFIT:   Breaks when you rename a variable or extract a helper.
              → Tests internal state, method call counts, exact implementation shape

❌ OUTDATED:  Tests for code that was deleted or behavior that changed.
              → Importing non-existent modules, testing deprecated API shapes

❌ SLOW/DEAD: Takes >3s and has zero unique coverage vs lower-level tests.
              → Measure. If no unique value, delete or convert to unit.
```

### Step 2 — Verdict Per Test

| Verdict | Meaning | Action |
|---|---|---|
| 🔴 DELETE | No behavior value, misleading, or duplicate | Remove + commit message explains why |
| 🟡 REWRITE | Good intent, bad form | Fix assertion, scope, or name |
| 🟢 KEEP | Proves real, unique behavior at right layer | No action |
| ⏸ ESCALATE | Skip/ignore needs human decision | Flag with full context |

### Step 3 — Never Silently Delete

For every deleted test, write a one-liner in the commit:
```
test: remove test_X — hollow (asserted mock return, not real behavior)
test: remove test_Y — redundant with test_Z which covers same contract
```

---

## OPTIMIZE PROTOCOL: Make CI Fast Based on Evidence, Not Vibes

### Step 1 — Measure Before Touching Anything

```bash
# Jest
npx jest --verbose 2>&1 | grep -E "PASS|FAIL|●" | sort -t ' ' -k2 -n

# Pytest
pytest --durations=20

# Go
go test ./... -v 2>&1 | grep -E "^(=== RUN|--- PASS|--- FAIL|FAIL)\s"
```

Produce a timing table:
| Test file | Time (s) | Lane | Unique coverage |
|---|---|---|---|

### Step 2 — Design Blast Radius CI Routing

```yaml
# GitHub Actions blast-radius config example
on:
  pull_request:
    paths:
      - 'src/domain/**'    # → run: core
      - 'src/api/**'       # → run: core, api
      - 'src/db/**'        # → run: core, db
      - 'src/ui/**'        # → run: ui
      - 'src/**'           # → run: core, api, db, integration

# Full suite (e2e + all lanes) triggers ONLY on:
#   → merge to main
#   → release branch
#   → manual trigger
```

### Step 3 — Parallelize & Cache

```
- Split DB tests across isolated workers (each with own schema)
- Mock external HTTP in unit/integration; real calls only in e2e
- Cache seed fixtures, compiled assets
- Shard large suites:
    jest --shard=1/4, 2/4, 3/4, 4/4
```

### Step 4 — Report Savings

```
Before: Xm Ys total CI time (all lanes, every PR)
After:  Xm Ys typical PR (blast-radius: core + api only)
Savings: XX% for most PRs
Full suite: release candidates only
```

---

## ULTRA OPTIMIZE PROTOCOL (`--ultra`)

Generate **5 independent, competing optimization strategies** for the current test suite.

For each strategy:
1. Describe the approach (lane split / parallelism / mock boundary / test pyramid reshape / etc.)
2. Estimate CI time reduction (conservative)
3. Estimate implementation effort (hours)
4. List tradeoffs

Then run a **Verifier pass**:
- Score each on: **Speed Gain / Risk / Maintenance Cost / Reversibility**
- Select the strongest strategy with written justification.
- Present the winner for human approval **before implementing anything**.

---

## ABSOLUTE RULES — NON-NEGOTIABLE

```
🚫 NEVER comment out a test before explaining WHY it fails.
   → Investigate first. Comment out only while actively fixing. Never ship commented tests.

🚫 NEVER skip a test without explicit human permission.
   → Escalate with reason. Do not unilaterally .skip or @Ignore.

🚫 NEVER weaken stable tests to make new code pass.
   → If a test breaks, fix the production code — not the test
   → (unless the behavior contract genuinely changed, and you can explain why)

🚫 NEVER write tests that exist only to raise coverage metrics.
   → Coverage is a side effect of testing real behavior, not the goal.

🚫 NEVER test implementation details.
   → Rename a private method. If a test breaks, that test was wrong.
```

---

## FAILURE INVESTIGATION PROTOCOL

When a test fails, work through this sequence **before touching any code**:

```
1. READ the failure message in full. What exactly failed?
2. Is this pre-existing, or did my change cause it?
3. If my change caused it:
   a. Did I intentionally change the behavior?
      → YES: Update the test to reflect the new contract. Document why.
      → NO:  Fix the production code. The test was right.
4. If pre-existing failure:
   → Do not fix unless in scope. Log it. Escalate to human.
5. Never: comment out / .skip / delete a failing test to make CI green
   without completing steps 1–4.
```

---

## SUMMARY CHEAT SHEET

```
/test create          → Scout (5 questions) → Assign lane → Write → RED → GREEN
/test audit           → Scan (6 categories) → Verdict each → Delete with reason
/test optimize        → Measure → Blast radius routing → Parallelize → Report
/test optimize --ultra → 5 strategies → Verifier scores → Human approves winner
```
