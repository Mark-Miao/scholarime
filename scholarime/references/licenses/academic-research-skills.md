# Academic Research Skills Attribution

Scholarime adapts the systematic-review screening workflow from:

- Project: Academic Research Skills
- Repository: https://github.com/Imbad0202/academic-research-skills
- Version: repository snapshot at commit `5fd230a44a0ee089674d96517cb65ea52694c8d8`
- Author: Cheng-I Wu (Imbad0202)
- License: Creative Commons Attribution-NonCommercial 4.0
- License text: `scholarime/vendor/academic_research_skills/LICENSE`

Scholarime vendors the `sr-screener` scripts, prompt templates, workflow templates, and selected
method references needed for deterministic record preparation, blinded screening prompts,
adjudication, quality-control rechecks, PRISMA counts, RIS groups, and literature-corpus output.
The broader Academic Research Skills writing pipeline, orchestration layer, hooks, evaluations,
and generated examples are not included.

Changes made for Scholarime:

- Added a `scholarime screening` command that accepts Scholarime search and review JSON.
- Connected prepared records and screening artifacts to Scholarime research workspaces.
- Added workspace-compatible selection exports from screening decisions.
- Replaced Claude-specific output labels with Scholarime labels while retaining upstream credit.

The upstream project does not endorse Scholarime. Adapted material remains noncommercial under the
upstream CC BY-NC 4.0 terms. The repository-level CC BY-NC-SA 4.0 license does not broaden those
permissions.
