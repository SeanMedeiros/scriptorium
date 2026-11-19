# TEI export mapping

Exports are validated against the TEI P5 4.12.0 `tei_all` RELAX NG schema (vendored in
`backend/app/exports/schemas/`). Validation proves conformance to `tei_all` only; it does not
claim conformance to a project-specific customisation (ODD).

## Document structure

| SCRIPTORIUM data | TEI |
|---|---|
| Document title | `teiHeader/fileDesc/titleStmt/title` |
| Researchers who edited (revision authors) | `titleStmt/respStmt[resp='Transcription and correction']/persName` |
| Recognition model(s) | `titleStmt/respStmt[resp='Automatic text recognition']/name` and `encodingDesc/appInfo/application[@ident='model-<slug>']` with `ptr/@target` = `https://doi.org/<DOI>`; `@version` only when the label is a version number (e.g. 1.6.2), otherwise it is given in `label` |
| Kraken version | `appInfo/application[@ident='kraken']/@version` |
| Archival reference (shelfmark) | `sourceDesc/msDesc/msIdentifier/idno` (otherwise `sourceDesc/bibl`) |
| Description / date / provenance | `msDesc/msContents/p`, `history/origin/origDate` (`@notBefore`, `@notAfter` from the recorded years), `history/provenance` |
| Languages | `profileDesc/langUsage/language/@ident` |
| Transcription policy | `encodingDesc/editorialDecl/p` |
| Revisions | `revisionDesc/listChange/change/@when` |
| Page image and line polygons | `facsimile/surface` (`graphic/@url`, pixel `@lrx/@lry`) and `zone/@points` |
| Page / line | `pb/@n` (+`@facs`), `lb/@n` (+`@facs`) inside one `ab` per page |
| Page never corrected | `note[@type='status']` "Uncorrected HTR output" |

## Editorial markup → TEI

| Markup v1 | TEI |
|---|---|
| `{?text?}` | `<unclear>` (optional `@cert="low"`) |
| `{illeg}` / `{illeg:N}` | `<gap reason="illegible"/>` (`@unit="chars" @quantity="N"`) |
| `{+text+}` | `<supplied>` |
| `{-text-}` | `<del>` |
| `{abbr\|expansion}` | `<choice><abbr/><expan/></choice>` (or `<abbr>` / `<expan>` only) |
| `{^text^}` | `<hi rend="superscript">` |
| normalised layer (option) | `<choice><orig>…</orig><reg>…</reg></choice>` per line |
| line note (option) | `<note type="editorial">` |

## Options (API query parameters / export dialog)

`tei_abbreviations` = `choice` (default) | `abbr` | `expan`; `tei_cert` (bool);
`tei_facsimile` (default true); `tei_normalized` (default false); `tei_notes` (default true).
The `layer` is `corrected` (markup encoded) or `htr` (model output, plain text).
