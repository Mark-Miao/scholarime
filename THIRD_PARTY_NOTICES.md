# Third-Party Notices

Scholarime includes or adapts material from the following projects.

Original Scholarime code and documentation are offered under CC BY-NC-SA 4.0. The material below
remains under its own license and is not relicensed by the project license.

## Citation Style Language Styles

The files `scholarime/assets/citation-styles/apa.csl` and
`scholarime/assets/citation-styles/gbt7714-2025-numeric.csl` come from the
[Citation Style Language styles repository](https://github.com/citation-style-language/styles).
Each file identifies its own contributors and license in its metadata. They are licensed under
Creative Commons Attribution-ShareAlike 3.0.

## lieflat-less-ai-tone

Selected response-level anti-AI-tone ideas are adapted from
[lieflat-less-ai-tone](https://github.com/larashero3-dotcom/lieflat-less-ai-tone) by shiujan.
The upstream work is licensed under Creative Commons Attribution-NonCommercial 4.0.

Scholarime includes the upstream prompt, detection protocol, semantic criteria, research notes,
worked example, and license under `scholarime/references/less-ai-tone/`. It does not include the
upstream detector scripts or marketing images. The adapted style rules may not be used commercially
unless the required permission is obtained. The upstream author does not endorse Scholarime. See
`scholarime/references/licenses/lieflat-less-ai-tone.md` for the adaptation record.

## External Proof Tools

Lean, Lake, Mathlib, Coq, and Rocq are optional external tools and are not distributed with this
repository. Their own licenses apply.

## statsmodels

Statsmodels is an optional runtime dependency and is not vendored into this repository. It is
distributed under the BSD-3-Clause license. Scholarime uses its public Python API for regression,
diagnostics, multiple-comparison correction, time-series analysis, and power calculations. The
upstream project does not endorse Scholarime. Integration was reviewed against repository commit
`39a9456e5824cddff812143843b9201a293a95c5`; production installs use the version range declared in
`pyproject.toml`.

## CiteVerify

The citation metadata lookup engine under `scholarime/vendor/citeverify/` is adapted from
[CiteVerify](https://github.com/cuihuash/CiteVerify) by Cuihua (Cindy) Shen. The vendored source is
version 0.1.4 at upstream commit `1967974bbd4a9ad2e6f86510346d773d7ed1f309`.

Only `citeverify_lookup.py` is included. Scholarime supplies its own candidate adapter and does not
bundle CiteVerify's PDF parser, web interface, or PyMuPDF dependency. The vendored source remains
licensed under the MIT License; the complete license text is stored at
`scholarime/vendor/citeverify/LICENSE`. Scholarime's modifications and integration code remain
under CC BY-NC-SA 4.0. See
`scholarime/references/licenses/citeverify.md` for the attribution record.

## nature-skills

Selected publication-figure tools are vendored from
[nature-skills](https://github.com/Yuan1z0825/nature-skills) at commit
`a65a8364b419e05f7d8d5d96842a1ff7e544b1bb`. The selected code remains under Apache License 2.0;
the full license is stored at `scholarime/vendor/nature_skills/LICENSE`. Scholarime does not include
the upstream image assets, generated examples, or AI image-generation route. See
`scholarime/references/licenses/nature-skills.md` for the adaptation record.

## Academic Research Skills

The systematic-review screening tool is adapted from
[Academic Research Skills](https://github.com/Imbad0202/academic-research-skills) at commit
`5fd230a44a0ee089674d96517cb65ea52694c8d8`, by Cheng-I Wu. The selected scripts, templates, and
method references remain under Creative Commons Attribution-NonCommercial 4.0; the full license is
stored at `scholarime/vendor/academic_research_skills/LICENSE`. Scholarime does not include the
broader writing pipeline, hooks, evaluations, or generated examples. See
`scholarime/references/licenses/academic-research-skills.md` for the adaptation record.
