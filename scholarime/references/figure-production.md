# Publication Figure Production

Scholarime has two figure paths. Use `scholarime chart` for CSV-driven bar, line, pie, or scatter
charts that need direct control of headers, legends, colors, fonts, and PNG output. Use
`scholarime figure template` when the correct scientific form is a volcano plot, ROC curve,
dot plot, marginal distribution, paired comparison, heatmap, or forest plot.

## Template Figures

```bash
scholarime figure list-templates
scholarime figure template volcano \
  --input results.csv \
  --output figures/volcano \
  --font-size 7
```

Templates write PNG, SVG, PDF, 600 dpi TIFF, and a `.qa.json` record. Production runs require a
CSV input. `--demo` exists only for smoke tests and is marked in the QA record.

Supported templates:

- `volcano`: effect size against adjusted significance, with explicit thresholds.
- `roc`: one false-positive-rate column and one or more true-positive-rate columns.
- `dotplot`: row category, column category, size, and color matrix.
- `marginal`: joint scatter with marginal distributions.
- `paired`: exactly two conditions with explicit pair identifiers.
- `heatmap`: row-by-column matrix with explicit optional ordering and visible missing cells.
- `forest`: effect estimates and confidence intervals against an explicit reference value.

The default font is Times New Roman with serif fallbacks. Set `--font` and `--font-size` explicitly
when a journal template requires another type system. Report the requested font and the fact that
the output includes editable SVG text. The QA record stores both the requested and resolved font.

## Quality Gates

Run source validation before rendering a plotting script:

```bash
scholarime figure validate-source figure.py --strict
```

Audit the final exports:

```bash
scholarime figure collision figure.pdf --json-out collision.audit.json
scholarime figure pdf-text figure.pdf --min-pt 5 --json
scholarime figure svg schematic.svg --json
scholarime figure panel-alignment panel-layout.json --strict
```

PDF collision auditing requires the optional `figure-pdf` dependency because it uses PyMuPDF.
The other audits and the source validator use the standard library, NumPy, or Matplotlib.

An automatic PASS does not establish scientific validity. Check the experimental unit, statistical
encoding, error bars, sample size, panel roles, legend wording, image resolution, and source-data
traceability against the manuscript. Never use a decorative template to imply a relationship the
data do not estimate.
