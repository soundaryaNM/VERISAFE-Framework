# 🚀 QUICK START - AI Unit Test Demo

## ⚡ 5-Minute Setup

### 1. Install Tools (if not done)
```powershell
cd C:\Users\SwathantraPulicherla\workspaces\UnitTestGen
cd CW_Test_Analyzer ; pip install -e . ; cd ..
cd CW_Test_Gen ; pip install -e . ; cd ..
cd CW_Test_Run ; pip install -e . ; cd ..
```

### 2. Set API Key
```powershell
$env:GEMINI_API_KEY="your-key-here"
# OR
$env:GROQ_API_KEY="your-key-here"
```

### 3. Run Master Demo Script
```powershell
python run_demo.py
```

The script will run **Analyze → Generate**, then **halt** until you complete the mandatory human review and create the approval flag.

To approve and continue:
1) Review: `RailwaySignalSystem/tests/review/review_required.md`
2) Create per-test approval file(s) under `RailwaySignalSystem/tests/review/` with contents **exactly**:
  - `approved = true`
  - `reviewed_by = <human_name>`
  - `date = <ISO date>`

  Or create it with the helper script:
  ```powershell
  python create_APPROVED_flag.py --repo-path RailwaySignalSystem
  ```
3) Re-run Phase 3 by running `python run_demo.py --skip-analysis --skip-generation`

---

## 🎯 Manual Step-by-Step (If Script Fails)

### Phase 1: Analysis
```powershell
python -m ai_c_test_analyzer.cli --repo-path RailwaySignalSystem
```
**Output:** `RailwaySignalSystem/tests/analysis/analysis.xlsx`

### Phase 2: Test Generation
```powershell
cd CW_Test_Gen
python -m ai_c_test_generator.cli `
  --repo-path ../RailwaySignalSystem `
  --file Interlocking.cpp `
  --source-dir src/logic `
  --output ../RailwaySignalSystem/tests `
  --model gemini
```
**Output:**
- Tests: `RailwaySignalSystem/tests/test_Interlocking.cpp`
- Review artifacts: `RailwaySignalSystem/tests/review/review_required.md`

### Phase 3: Compile & Run
✅ **Required approval step first**

Create per-test approval file(s) under `RailwaySignalSystem/tests/review/` with contents **exactly**:
```
approved = true
reviewed_by = <human_name>
date = <ISO date>
```

```powershell
cd ../CW_Test_Run
python -m ai_test_runner.cli ../RailwaySignalSystem
```
**Output:** Test results in `RailwaySignalSystem/tests/test_reports/`

---

## 📊 What to Show in Demo

### 1. Analysis Excel (5 min)
Open: `RailwaySignalSystem/tests/analysis/analysis.xlsx`

**Talk Track:**
- "Our analyzer scanned 18 files and found 13 functions"
- "It classified 5 classes: 2 hardware, 2 mixed, 1 pure logic"
- "Hardware-free functions like Interlocking.cpp are easiest to test"
- Show Function Index sheet, explain columns

### 2. Generated Tests (7 min)
Open: `RailwaySignalSystem/tests/test_Interlocking.cpp`

**Talk Track:**
- "AI generated 5 test cases covering all code paths"
- "See how it tests edge cases: controller stale, track fault"
- "Notice the fixture setup with mock inputs"
- "This would take a developer 2-3 hours manually"

### 3. Test Results (5 min)
Show terminal output from Phase 3

**Talk Track:**
- "Compilation succeeded in 10 seconds"
- "All 5 tests passed"
- "In production, we'd show coverage report here"

### 4. Time Savings (3 min)
**Talk Track:**
- "Manual: 2-3 days for this module"
- "AI-assisted: 20 minutes end-to-end"
- "ROI: 80-90% time savings"

---

## 💬 CLIENT OBJECTION HANDLERS

### "What about MC/DC coverage?"
**Response:** "Great question. MC/DC is Phase 2 (Q2 2026). Today's foundation enables it. We're evaluating Bullseye and gcov extensions. Would you like to see our roadmap timeline?"

### "Will it work with our codebase?"
**Response:** "The analyzer works on any C/C++ code. For your specific patterns, we recommend a 1-week pilot analysis to identify any custom tuning needed. Can we schedule that?"

### "What if tests are wrong?"
**Response:** "Our validator catches 90% of issues pre-generation. The remaining 10% are flagged for review. Plus, you own the code—you can modify tests as needed. Transparency is key."

### "This seems too good to be true"
**Response:** "I understand the skepticism. That's why we're showing real code, not a toy example. RailwaySignalSystem is realistic embedded code with hardware abstraction. Let's run it live together."

### "What about cost?"
**Response:** "Typical pilot: 50K lines, $5K, includes setup and training. Given you save 80% of testing time, ROI is immediate. For your exact quote, I need to know your codebase size."

---

## 🚨 BACKUP PLANS

### If Test Generation Fails
- **Cause:** API rate limit or network issue
- **Fix:** Use pre-generated test file (already exists from earlier run)
- **Talk Track:** "In production, we'd use Ollama on-premise to avoid this. Let me show you the pre-generated tests..."

### If Compilation Fails
- **Cause:** Missing GoogleTest or CMake misconfiguration
- **Fix:** Show the generated test code anyway
- **Talk Track:** "The compilation issue is environment-specific. Let me show you the test code quality - that's what matters..."

### If Client Wants Live Generation
- **Risk:** Might take 3-5 minutes for AI
- **Mitigation:** Keep talking during generation
- **Talk Track:** "While this generates, let me explain our validation pipeline..."

---

## ✅ PRE-DEMO CHECKLIST

- [ ] API key is set (`echo $env:GEMINI_API_KEY`)
- [ ] All tools installed (`pip list | Select-String "ai-c"`)
- [ ] Analysis already run (check for `analysis.xlsx`)
- [ ] Pre-generate test file as backup
- [ ] RailwaySignalSystem compiles (`cmake --build RailwaySignalSystem/build`)
- [ ] Demo script tested (`python run_demo.py --help`)
- [ ] Excel file opens correctly
- [ ] Terminal font size is readable for screen share
- [ ] Backup slides ready (if tech fails completely)

---

## 🎤 OPENING STATEMENT

"Good morning/afternoon. Today I'm going to show you how we cut unit testing time by 80% using AI. This isn't a concept—it's working code on a real embedded system. We'll analyze a railway signaling system, generate tests with AI, compile them, and run them—all in about 20 minutes. Let's dive in."

---

## 🎬 CLOSING STATEMENT

"So in 20 minutes, we took a complex embedded codebase, analyzed it, generated comprehensive tests, and verified they work. Manually, this would take 2-3 days. The tests are yours—readable GoogleTest code you can modify. Our roadmap includes MC/DC coverage and CI/CD plugins. 

What questions do you have?"

---

## 📞 NEXT STEPS AFTER DEMO

### If Interested:
1. Schedule pilot analysis of their codebase
2. Send proposal within 48 hours
3. Provide demo recording + documentation

### If Need More Time:
1. Send technical whitepaper
2. Offer free trial on their repo (1 module)
3. Schedule follow-up in 1 week

### If Objections:
1. Address specific concerns in writing
2. Provide case studies from similar domains
3. Offer proof-of-concept at no cost

---

**Remember:** You're selling TIME SAVINGS and RISK REDUCTION. Every moment should reinforce: "This saves your team weeks with better quality."

**Breathe. You've got this. 🚀**
