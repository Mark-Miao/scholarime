# 去 AI 味 Tool

Use this tool when the user explicitly asks to:

- 去 AI 味、降低 AI 味、降低 AI 率、去 AI 腔
- make text sound less AI-generated or less templated
- rewrite supplied text in a more natural human register without changing its substance

Do not use it for ordinary copyediting, translation, summarization, or a normal Scholarime
response.

## Required Reading

Read these files completely before rewriting:

1. [UPSTREAM-SKILL.md](UPSTREAM-SKILL.md) for the complete original rules, exceptions, rewrite
   principles, and final acceptance criteria.
2. [DETECT.md](DETECT.md) for detection categories and the boundary between automatic edits,
   report-only metrics, rejected rules, and human review.
3. [references/semantic-criteria.md](references/semantic-criteria.md) for the four closed semantic
   judgments.

Use [RESEARCH.md](RESEARCH.md) only when evidence or a rule's provenance needs clarification.

## Prompt-Only Execution

This integration is prompt-only. Do not call the upstream Python detection or reporting scripts.
Perform the checks directly against the supplied text using the same categories, exclusions, and
evidence rules. Do not claim a measured percentage or a statistically significant result unless
the text was actually measured.

The complete upstream rewriting rules remain authoritative, with one delivery override: this tool
returns the processed text directly instead of issuing the upstream pre-edit report and waiting for
another confirmation. If the user explicitly asks for a detection report or before/after
comparison, follow the original reporting workflow instead of returning only rewritten text.

## Processing Contract

If the user has not supplied the source text, ask for it and stop.

Before editing, identify only the applicable high-confidence patterns. Apply the smallest change
that resolves each real issue. Preserve the author's meaning, facts, names, numbers, dates,
qualifiers, citations, terminology, headings, paragraph order, lists, tables, code, formulas, and
requested format. Do not add personal experience, emotion, colloquial filler, or artificial sentence
length variation.

Never treat ordinal headings, meaningful questions, "这意味着", normal repetition, complete
enumerations, quotations, or metaphors as automatic defects. Leave human-review-only material
unchanged unless the user explicitly requests work on that material.

Re-check the rewritten text against the upstream final acceptance criteria. If nothing genuinely
needs changing, return the supplied text unchanged.

## Final Delivery

Return only the complete processed text in the user's original language and format. Do not add a
change summary, score, model attribution, detection table, preface, conclusion, Markdown wrapper
fence that was not in the source, or an offer of further help.

The upstream material is licensed under CC BY-NC 4.0. See [LICENSE.txt](LICENSE.txt). If the user
explicitly states that the work is for a commercial product or client delivery, check the upstream
commercial licensing requirement before proceeding.
