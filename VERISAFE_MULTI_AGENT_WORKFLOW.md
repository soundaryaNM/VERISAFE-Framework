# VERISAFE – Multi-Agent Workflow (Final)

## Core principle

Sequential, gated, deterministic pipeline.
No parallel autonomy. No hidden decisions. No execution without approval.

AI assists planning and synthesis only — never safety decisions.

---

1️⃣ Analyzer (Deterministic, Ground Truth)

Role: Extract facts — nothing else.

Inputs

- Source code (.c / .cpp / .h)

Outputs (analysis.json / repo_scan.json)

- file_index
- function_index
- call_graph + call_depths
- extracted function bodies
- hardware-touching flags
- class / role classification
- schema_version

Rules

- No inference
- No guessing
- No AI
- Only real functions (no if, switch, macros)

This is the only source of truth

⛔ If Analyzer is wrong → entire pipeline is invalid.

---

2️⃣ Safety Policy Loader (Deterministic)

Role: Define the law of the system.

Inputs

- safety_policy.yaml (QM / SIL2 / SIL3 / SIL4)

Output

- Planning Constraint Object (PCO)

Rules

- Loaded once
- NEVER passed raw to any LLM
- Enforced at planning, validation, execution

Example constraints:

- Branch coverage required
- MC/DC optional
- Hardware code excluded
- Human approval mandatory

---

3️⃣ Scenario Architect (LLM, Highly Constrained)

Role: Decide what deserves to be tested.

Inputs

- Analyzer output
- Planning Constraint Object

Output

- scenarios.json (safety obligations)

Key idea

A scenario ≠ function ≠ line of code
A scenario = safety-relevant decision

Rules

- Only analyzer-derived functions
- Only .cpp logic
- No headers
- No constructors
- No HAL / hardware glue

Each scenario:

- references a real decision
- ties to a policy clause
- is auditable and reviewable

⛔ Invalid scenario → rejected immediately

---

4️⃣ Scenario Validator (Deterministic Gate)

Role: Enforce scenario correctness.

Checks

- Function exists in analyzer output
- Decision exists in function body
- Inputs are real struct members
- Policy justification present
- Schema compliance

Output

- validation_report.json

⛔ Any failure → pipeline stops

---

5️⃣ Test Coder (LLM, Constrained)

Role: Convert one scenario → one test block

Inputs

- Single scenario
- Analyzer data (types, headers)
- Policy constraints

Output

- Raw test code (not trusted yet)

Rules

- No refactoring production code
- No invented values
- Real headers, real types only
- Deterministic expectations
- No markdown required (but may appear)

---

6️⃣ Cleanup / Normalizer (LLM or Deterministic)

Role: Mechanical cleanup only.

Purpose

Turn “LLM-shaped code” into certifiable C++

Fixes

- Namespace qualification
- Single-evaluation rule
- Remove helper test functions
- Flatten fixtures
- Deduplicate includes
- Remove markdown fences

🚫 No semantic changes allowed

---

7️⃣ Test Validator (Deterministic Gate)

Role: Hard correctness enforcement.

Checks

- Compilable C++
- Valid GoogleTest syntax
- No duplicate includes
- No multiple calls to same function per test
- No unqualified symbols
- Policy compliance

Output

- validation_report.json

⛔ Any failure → pipeline stops

---

8️⃣ Enforcer (Human-in-the-Loop)

Role: Prevent autonomous execution.

Requirements

- Explicit approval file
- Hash match of approved content
- Reviewer metadata
- Timestamp

🚫 This gate is non-optional by design

---

9️⃣ Runner (Blocked by Default)

Role: Execute tests.

Condition

- Only runs if Enforcer approval is present

Outputs

- Build logs
- Test results
- Evidence artifacts

---

10️⃣ Orchestrator (Deterministic Controller)

Pipeline

analyze
→ load_policy
→ plan_scenarios
→ validate_scenarios
→ generate_tests
→ cleanup
→ validate_tests
→ await_approval
→ compile
→ run

Rules

- Each stage writes artifacts to work/
- JSON schema validation at every boundary
- Fail fast
- No silent fallbacks

---

*Document created from the agreed VERISAFE multi-agent workflow summary.*
