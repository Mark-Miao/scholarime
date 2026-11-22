# Citation Verification

Scholarime uses a vendored copy of CiteVerify's metadata lookup engine to check search
candidates before presenting them. This step looks up DOI metadata, books, and reports in
Crossref, the DOI resolver, Open Library, and Google Books. OpenAlex and Semantic Scholar can
cross-check DOI records when their API keys are configured.

Verification is evidence, not a verdict. It does not prove that a work is genuine, that its
contents support a claim, or that the retrieved metadata is complete. Search results without a
DOI often remain unverified because no strong identifier is available.

## Automatic Workflow

Run verification after `academic_search.py` creates `candidates.json` and before selecting the
final references:

```bash
scholarime verify /tmp/scholarime-search-.../candidates.json
```

The tool writes `citation-verification.json` and an HTML report beside the input. It also prints
status counts. Inspect the candidates and their verification evidence one by one. Do not replace
relevance judgment with citation verification; a verified record can still be irrelevant.

When the user requests citation formatting, the reference review UI receives the original
`candidates.json`. Preserve each selected record's verification status from
`citation-verification.json` when presenting or exporting the final list.

## Status Meanings

- `verified`: a lookup source returned a strong title match.
- `verified_title_variant`: the title matches substantially but differs in wording, subtitle, or
  edition. Review the variant.
- `conflict`: the DOI service responded but the returned title does not match, or a DOI was not
  found. Flag this explicitly and do not silently treat the reference as confirmed.
- `unverified`: no sufficient match was found. This is common without a DOI and means only that
  Scholarime could not confirm the metadata.
- `unable_to_check`: a lookup was attempted but services failed or were unavailable because of
  network errors or rate limits. This is not evidence of fabrication.

The order is attention-oriented, not a truth ranking. `unverified` and `unable_to_check` records
must not be deleted solely because of their status. Retain relevant records and explain the
evidence limitation. A `conflict` record requires explicit user-visible warning and manual review
before citation.

## Field-Level Verification

Each result also carries `verification.field_comparison` and `field_verification_status`. The
field comparison checks retrieved title, authors, year, container, volume, issue, and pages against
the search candidate. Its statuses are:

- `consistent`: no retrieved field conflicts, and at least one field was checked.
- `review`: a title variant, author-order difference, year difference of at most one, or related
  container wording needs manual review.
- `conflict`: at least one retrieved field conflicts. Do not silently cite the record.
- `insufficient_metadata`: the lookup did not return enough fields for a comparison.

Field comparison does not make the CiteVerify existence status more certain. It separates
"the record exists" from "this citation describes the record accurately." Missing fields remain
`not_checked` rather than being treated as matches or errors.

## API Keys and Privacy

OpenAlex and Semantic Scholar keys are optional. The tool reads OpenAlex keys from
`OPENALEX_KEY` or `--openalex-key-file`, and Semantic Scholar keys from `S2_API_KEY`,
`SEMANTIC_SCHOLAR_API_KEY`, the Scholarime user configuration, or `--s2-api-key-file`.

Never place API keys in shell arguments, chat messages, committed files, reports, or logs.
Prefer a protected local key file or environment variable. Verification sends limited metadata,
such as a DOI or title, to the enabled lookup services. It does not upload PDFs or invoke a large
language model.

## Output Contract

The JSON artifact records the source file hash, timestamp, diagnostics passed to CiteVerify,
status counts, verification evidence, and enriched search candidates. Reports and JSON may
include titles, authors, DOI values, and lookup errors, so review them before sharing publicly.
The tool refuses to replace existing outputs unless `--overwrite` is explicitly supplied.
