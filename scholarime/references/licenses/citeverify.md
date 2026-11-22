# CiteVerify Attribution

Scholarime vendors the citation metadata lookup engine from:

- Project: CiteVerify
- Repository: https://github.com/cuihuash/CiteVerify
- Author: Cuihua (Cindy) Shen
- Version: 0.1.4
- Upstream commit: `1967974bbd4a9ad2e6f86510346d773d7ed1f309`
- License: MIT
- License text: `scholarime/vendor/citeverify/LICENSE`

Scholarime copied `citeverify_lookup.py` without its PDF and Word parser, web interface, launcher
scripts, or PyMuPDF dependency. The integration adapter converts Scholarime search candidates
into CiteVerify lookup diagnostics and enriches the candidates with verification evidence.
Scholarime also extends the vendored metadata extraction to retain year, container, volume, issue,
and page fields for field-level citation comparison.

The upstream project does not endorse Scholarime. The vendored file remains governed by its MIT
license. Scholarime's adapter and documentation remain under CC BY-NC-SA 4.0.
