# AI-Assisted Development Slide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one readable AI-assisted development workflow slide after the architecture slide while preserving the existing deck style and updating all page numbers.

**Architecture:** Import the v5 deck with `@oai/artifact-tool`, duplicate the existing workflow slide as the visual source, move the duplicate to index 4, and rewrite/reposition inherited elements into a four-role flow. Export a new v6 deck and validate all 11 rendered slides, overflow, and template fidelity.

**Tech Stack:** JavaScript ES modules, `@oai/artifact-tool`, bundled presentation rendering and QA scripts.

## Global Constraints

- Preserve `AIPE04_台北停車地獄雷達_期中專題_v5_優化第6頁排版.pptx` unchanged.
- Insert the new slide after the current slide 4; final deck contains exactly 11 slides.
- Visible copy uses: Superpowers, GPT-5.6 SOL, DeepSeek V4 Flash, and 專題負責人.
- The final takeaway is: `AI 負責加速產出，我負責理解、判斷與驗證。`
- Reuse inherited template elements and keep the current dark visual system.
- Output only the final PPTX outside the temporary build directory.

---

### Task 1: Add and verify the AI-assisted development slide

**Files:**
- Create: `.tmp/parking-radar-ppt-v6/edit_v6.mjs`
- Create: `.tmp/parking-radar-ppt-v6/template-frame-map.json`
- Create: `output/AIPE04_台北停車地獄雷達_期中專題_v6_AI開發流程.pptx`

**Interfaces:**
- Consumes: the 10-slide v5 PPTX and inherited elements from its workflow slide.
- Produces: an 11-slide v6 PPTX with the new slide at 1-based position 5.

- [ ] **Step 1: Inspect the source workflow slide**

Run an artifact-tool inspection for slide 7 and record the exact inherited title, workflow boxes, arrows, bottom panel, and page-number elements in the frame map.

- [ ] **Step 2: Verify the source deck baseline**

Render the unchanged v5 deck and confirm it contains 10 slides before duplication.

- [ ] **Step 3: Implement the focused deck edit**

In `edit_v6.mjs`, import v5, duplicate slide 7, call `moveTo(4)`, rewrite the title and four workflow boxes, remove the unused fifth workflow box and arrow, reposition the retained boxes and arrows evenly, rewrite the bottom conclusion, set speaker notes, and update every visible page number to `01` through `11`.

- [ ] **Step 4: Export and inspect the new slide**

Export the new slide PNG and layout JSON, then verify the four roles appear in this order: Superpowers → GPT-5.6 SOL → DeepSeek V4 Flash → 專題負責人.

- [ ] **Step 5: Run full-deck QA**

Render all 11 slides, inspect each slide, run `slides_test.py`, and run template fidelity validation. Expected results: 11 rendered slides, no overflow, no unintended overlap, and zero fidelity issues.

- [ ] **Step 6: Deliver the versioned deck**

Return only the v6 PPTX as the presentation output and keep v5 unchanged.
