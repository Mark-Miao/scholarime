# Scholarime

**English** | [简体中文](README.zh-CN.md)

Scholarime is a source-available academic research agent toolkit. It provides a Codex skill and a
set of local Python tools for literature search, systematic-review screening, evidence organization,
data analysis, symbolic computation, formal proof checking, citation verification, manuscript
assembly, and publication figures.

Scholarime is not OSI open source because commercial use is restricted. See [License](#license).

For better source rate limits, set a contact email and optional API credentials before searching:

```bash
export SCHOLARIME_CONTACT_EMAIL="you@example.org"
export NCBI_API_KEY="your-ncbi-key"   # optional
```

## Main Capabilities

| Command | Purpose |
|---|---|
| `scholarime search` | Search OpenAlex, Crossref, arXiv, and PubMed; query Semantic Scholar when an API key is configured |
| `scholarime verify` | Check search candidates against DOI, DOI resolver, book, and optional scholarly-index metadata |
| `scholarime review` | Review, reorder, extend, import, and cite selected references in a local WebUI |
| `scholarime screening` | Prepare records, generate blinded screening prompts, merge adjudications, and report PRISMA counts |
| `scholarime workspace` | Keep sources, authorized PDFs, evidence segments, claims, decisions, and artifacts in a project SQLite database |
| `scholarime data` | Profile, clean, transform, and audit tabular data with declarative JSON recipes |
| `scholarime stats` | Run descriptive, OLS/WLS/GLM/mixed-model, diagnostic, multiplicity, and time-series analyses |
| `scholarime symbolic` | Compute SymPy derivations and verify Lean 4 or Coq/Rocq proof sources |
| `scholarime manuscript` | Build LaTeX and DOCX manuscripts with figures, tables, and citations |
| `scholarime chart` | Generate academic PNG charts from CSV data |
| `scholarime figure` | Render volcano, ROC, dot, marginal, paired, heatmap, and forest templates; validate or audit exports |

When the user asks to 去 AI 味、降低 AI 率, or rewrite supplied text to sound less AI-generated,
Scholarime loads the complete original prompt rules and returns the processed text while preserving
facts, citations, and document structure.

CNKI, Wanfang, and VIP do not have approved automatic interfaces in this project. Their exported
RIS, BibTeX, EndNote, or RefWorks records can be imported into the reference review UI.
Source failures and partial coverage are reported explicitly. OpenAlex and Crossref use the contact
email for their polite request pools; PubMed uses it with `tool=Scholarime`.

After a search, Scholarime automatically runs CiteVerify-based metadata checks and keeps the
resulting status with each candidate. Verification is evidence, not a fake/real verdict:
`unverified` and `unable_to_check` do not prove fabrication, while `conflict` requires explicit
manual review. See
[scholarime/references/citation-verification.md](scholarime/references/citation-verification.md).

## Installation

Scholarime requires Python 3.10 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[all]"
```

On Windows, activate with `.\.venv\Scripts\Activate.ps1` or `.venv\Scripts\activate.bat`.
Optional dependency groups are available for smaller installations: `charts`, `figures`,
`figure-pdf`, `ui`, `excel`, `symbolic`, `workspace`, `stats`, and `writing`.

Full platform-specific instructions, mirror-selection guidance, font setup, and external tool
installation are in [scholarime/install/INSTALL.md](scholarime/install/INSTALL.md).

## Quick Start

```bash
scholarime --help
scholarime search "retrieval augmented generation" --limit 10
scholarime search "大语言模型 学术写作" \
  --query "large language models academic writing"
scholarime verify /tmp/scholarime-search-.../candidates.json
scholarime screening init review-screening
scholarime workspace init research-project --title "My project"
scholarime data profile data.csv
scholarime symbolic doctor
scholarime figure list-templates
```

The recommended research flow is:

1. Search for sources, verify metadata, and review the ranked candidates.
2. Create a research workspace and import selected references.
3. For systematic reviews, prepare and screen records against a confirmed protocol and PRISMA counts.
4. Attach authorized PDFs and extract page-level text, tables, or OCR evidence.
5. Link evidence to explicit claims and export an evidence matrix.
6. Clean and analyze data through reproducible recipes.
7. Use symbolic or formal verification where the research requires mathematical checking.
8. Assemble and review the final manuscript with explicit citations.

See [scholarime/references/research-pipeline.md](scholarime/references/research-pipeline.md) for
detailed workflows and schemas.

## Project Layout

```text
scholarime/
|-- SKILL.md                 Codex skill entrypoint
|-- scripts/                 Python tools
|-- references/              Workflow and format guidance
|-- assets/citation-styles/  APA and GB/T 7714-2025 CSL assets
|-- vendor/                  Audited third-party lookup, figure, and screening code
|-- formal/examples/         Small Lean and Coq examples
|-- install/                 Dependencies and installation helpers
`-- tests/                   Standard-library unittest suite
```

[AGENTS.md](AGENTS.md) defines repository-wide agent instructions.

## External Tools

Lean 4, Coq/Rocq, Tesseract language data, and native WebView libraries are not bundled. The
toolkit reports an integration as unavailable when its external dependency is missing.

- Lean: install elan, Lean, and Lake; use an existing Lake project with `--project`.
- Coq: Arch Linux provides `rocq` and `rocq-stdlib`; the verifier accepts `coqc` and `rocq compile`.
- OCR: install Tesseract plus the required language packs, such as `eng` or `chi_sim`.

## Safety and Privacy

Project workspaces are local. API keys are stored in the user configuration directory or supplied
through environment variables. Only process PDFs the user is authorized to use.

Generated statistical results, relevance rankings, formal statements, and manuscripts require
human review. Scholarime does not treat a symbolic simplification as a formal proof or a p-value
as a causal conclusion.

## Testing

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s scholarime/tests -v
```

Optional integration tests run when their dependencies are available.

## License

Original Scholarime code and documentation are licensed under
[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). Copyright (c) 2026
Scholarime contributors.

Commercial use is prohibited. Shared adaptations must use the same license and preserve
attribution. This is a source-available license, not an OSI open-source license. It does not
automatically require a modified network service to publish its source.

Third-party CSL styles, CiteVerify, nature-skills figure tools, Academic Research Skills screening
material, Statsmodels, and adapted style material retain their own licenses. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before redistributing the repository.
