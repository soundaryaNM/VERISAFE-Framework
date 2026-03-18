# MC/DC Coverage Roadmap & Strategy

## Current Status vs. Future Vision

### ✅ What We Have TODAY (Phase 1)
- **Line Coverage:** Via gcov/gcc `--coverage` flags
- **Branch Coverage:** Basic if/else branch tracking
- **Function Coverage:** Which functions are called
- **Integration:** Coverage data captured during test execution

### 🚧 What We're Building (Phase 2 - Q2 2026)
- **MC/DC Coverage:** Modified Condition/Decision Coverage
- **DO-178C Compliance:** Aviation safety standard support
- **Detailed Reports:** Per-condition analysis
- **Threshold Enforcement:** Fail builds below MC/DC targets

---

## What is MC/DC? (Client Explanation)

**MC/DC = Modified Condition/Decision Coverage**

### Simple Explanation
"MC/DC ensures that every condition in a complex boolean expression independently affects the outcome. It's required for safety-critical software in aviation (DO-178C Level A), automotive (ISO 26262), and medical devices."

### Example
```cpp
// Complex condition
if (speed > 100 && brake_pressed && !emergency_stop) {
    apply_full_brake();
}
```

**Line coverage** only checks: "Did we execute this line?"  
**Branch coverage** checks: "Did we take both paths (true and false)?"  
**MC/DC** checks: "Did EACH condition (speed, brake, emergency) independently affect the decision?"

### Why Clients Care
- **Regulatory:** Aviation/automotive standards REQUIRE MC/DC
- **Safety:** Catches subtle logic bugs that branch coverage misses
- **Insurance:** Lower liability with proven MC/DC compliance
- **Quality:** Industry gold standard for critical code

---

## Technical Implementation Plan

### Option 1: gcov Extension (Open Source)
**Tool:** `gcov` with custom scripting  
**Pros:** Free, integrates with existing GCC  
**Cons:** Requires manual test case design to achieve MC/DC  
**Timeline:** Q2 2026  
**Status:** Proof-of-concept in progress

### Option 2: Bullseye Coverage (Commercial)
**Tool:** Bullseye Coverage by Bullseye Testing Technology  
**Pros:** Full DO-178C compliance, detailed MC/DC reports  
**Cons:** Licensing cost (~$1,200/developer)  
**Timeline:** Q1 2026 (partnership negotiations)  
**Status:** Evaluation underway

### Option 3: LDRA Testbed (Enterprise)
**Tool:** LDRA suite  
**Pros:** Complete certification package, tool qualification  
**Cons:** Expensive ($10K+), complex integration  
**Timeline:** Q3 2026 (enterprise customers only)  
**Status:** On roadmap

### Option 4: VectorCAST (Automotive Focus)
**Tool:** VectorCAST/C++  
**Pros:** ISO 26262 certified, automotive proven  
**Cons:** Licensing cost, learning curve  
**Timeline:** Q2 2026  
**Status:** Evaluating

---

## Our Recommended Approach

### Phase 2A (Q1 2026): Basic MC/DC
- Integrate **gcov** with enhanced reporting
- Generate MC/DC test pairs automatically via AI
- Provide MC/DC percentage metrics
- **Cost:** Included in base product

### Phase 2B (Q2 2026): Certified MC/DC
- Partner with **Bullseye** for full DO-178C compliance
- Provide tool-qualified reports for certification authorities
- Support customer tool qualification processes
- **Cost:** Additional license fee (passed through)

### Phase 3 (Q3 2026): Enterprise Options
- LDRA integration for aerospace customers
- VectorCAST for automotive customers
- Custom tool qualification support
- **Cost:** Enterprise pricing

---

## AI's Role in MC/DC

### Challenge
MC/DC requires carefully designed test cases to achieve independence.

### Our AI Solution
1. **Analyzer Phase:** Identify all boolean conditions in code
2. **AI Generation:** Create test pairs that toggle each condition
3. **Validation:** Verify MC/DC independence requirements
4. **Reporting:** Show which conditions achieved MC/DC

### Example AI-Generated MC/DC Tests
```cpp
// Source code
bool is_safe_to_signal(bool track_clear, bool no_fault, bool timer_ok) {
    return track_clear && no_fault && timer_ok;
}

// AI generates 4 test pairs for MC/DC:
TEST(SafetyTest, TrackClear_Independent) {
    // Vary only track_clear
    EXPECT_FALSE(is_safe(false, true, true));  // track=F → false
    EXPECT_TRUE(is_safe(true, true, true));    // track=T → true
}

TEST(SafetyTest, NoFault_Independent) {
    // Vary only no_fault
    EXPECT_FALSE(is_safe(true, false, true));  // fault=F → false
    EXPECT_TRUE(is_safe(true, true, true));    // fault=T → true
}

TEST(SafetyTest, TimerOk_Independent) {
    // Vary only timer_ok
    EXPECT_FALSE(is_safe(true, true, false));  // timer=F → false
    EXPECT_TRUE(is_safe(true, true, true));    // timer=T → true
}
```

**Key Insight:** AI understands the dependency structure and generates the minimal test set for MC/DC.

---

## Timeline & Milestones

```
NOW (Jan 2026)
│
├─ ✅ Line/Branch Coverage Working
│
Q1 2026
│
├─ 🚧 Bullseye Partnership Agreement
├─ 🚧 Basic MC/DC Reporting (gcov)
├─ 🚧 AI MC/DC Test Pair Generation
│
Q2 2026
│
├─ 🎯 MC/DC Phase 2A Release
├─ 🎯 Bullseye Integration Complete
├─ 🎯 DO-178C Compliance Package
│
Q3 2026
│
├─ 🎯 LDRA Integration (Enterprise)
├─ 🎯 VectorCAST Support (Automotive)
└─ 🎯 Tool Qualification Support
```

---

## Demo Talking Points

### When Client Asks: "What about MC/DC?"

**Response:**
"Excellent question—MC/DC is on our Phase 2 roadmap for Q2 2026. Here's our strategy:

1. **Today:** We provide line and branch coverage, which handles 80% of use cases. Our architecture is MC/DC-ready.

2. **Q1 2026:** We're adding basic MC/DC reporting via gcov extensions, plus AI-generated test pairs that achieve independence.

3. **Q2 2026:** For DO-178C compliance, we're partnering with Bullseye to provide certified, tool-qualified MC/DC reports.

4. **Why the wait?** MC/DC requires certified tooling for regulatory acceptance. We're doing it right—with partnerships that ensure compliance, not just metrics.

5. **Pilot path:** We can start with line/branch coverage today, then upgrade to MC/DC in Q2 when your certification timeline requires it.

Would you like to see our detailed roadmap timeline?"

---

## Competitive Positioning

### Competitor: VectorCAST
- **Them:** Full MC/DC today, but $20K+ per seat, steep learning curve
- **Us:** Faster test generation, AI-assisted, MC/DC coming Q2, 1/4 the cost

### Competitor: LDRA Testbed
- **Them:** Enterprise-grade, DO-178C certified, but $50K+ deployment cost
- **Us:** Same end goal (certification), but modern AI workflow, phased approach

### Competitor: Manual MC/DC
- **Them:** Engineers manually design test pairs (weeks of work)
- **Us:** AI generates MC/DC pairs automatically (minutes)

### Our Advantage
"We're the **only** solution that combines AI test generation with a clear path to certified MC/DC coverage. You get speed today, compliance tomorrow."

---

## Risk Mitigation

### Client Concern: "We need MC/DC NOW"
**Response:** "We can fast-track Phase 2A for your project. With a dedicated engagement, we can deliver basic MC/DC in 6-8 weeks. Would that meet your timeline?"

### Client Concern: "Will your MC/DC be certified?"
**Response:** "Yes. Our Q2 release will use Bullseye, which is DO-178C tool-qualified. We'll provide the qualification evidence your DER/certification authority needs."

### Client Concern: "What if the roadmap slips?"
**Response:** "Fair concern. Our fallback: We provide integration guides for you to use Bullseye/LDRA directly with our generated tests. You're not locked in—our tests work with any coverage tool."

---

## Investment Justification

### Manual MC/DC (Current State)
- **Time:** 5-10 days per module
- **Cost:** $10K-20K in engineering time
- **Risk:** Human error in test pair design

### AI-Assisted MC/DC (Our Future)
- **Time:** 1-2 hours per module
- **Cost:** $500-1K in AI + tool licenses
- **Risk:** Automated validation catches errors

### ROI Calculation
- **50 modules** to certify
- **Manual:** 250-500 days ($250K-500K)
- **AI-assisted:** 50-100 hours ($5K-10K)
- **Savings:** $240K-490K per project

---

## Action Items for Client

### If MC/DC is Critical:
1. **Option A:** Wait for our Q2 release (4 months)
2. **Option B:** Pilot with line/branch coverage now, upgrade to MC/DC later
3. **Option C:** Fast-track development with dedicated engagement ($20K)

### If MC/DC is Nice-to-Have:
1. Start with our current offering (line/branch)
2. Revisit MC/DC in Q2 2026
3. No additional cost—it's part of the roadmap

---

## Summary Slide

**MC/DC: The Path Forward**

| Timeline | Feature | Status | Client Value |
|----------|---------|--------|--------------|
| **NOW** | Line/Branch Coverage | ✅ Ready | 80% of testing needs |
| **Q1 2026** | Basic MC/DC Reporting | 🚧 In Progress | Visibility into independence |
| **Q2 2026** | Certified MC/DC (Bullseye) | 🎯 Planned | DO-178C compliance |
| **Q3 2026** | Enterprise Options (LDRA) | 📋 Roadmap | Full certification support |

**Bottom Line:** Our Phase 1 solves 80% of the problem TODAY. Phase 2 delivers the remaining 20% for regulated industries in 4 months.

---

**Confidence Closer:**
"MC/DC is hard—that's why we're partnering with certified tools rather than reinventing the wheel. You get AI speed with certification rigor. Best of both worlds."

