# Vendored validation schemas

Used offline by `app.exports.validate`. Files are unmodified copies, except that the ALTO
schema's xlink import points to the local `xlink.xsd` (no network access during validation).

| File | Source | Licence |
|---|---|---|
| `alto-4-4.xsd` | https://raw.githubusercontent.com/altoxml/schema/master/v4/alto-4-4.xsd (ALTO 4.4, Library of Congress / ALTO Board) | ALTO schema, freely available (CC-BY-SA per altoxml/schema repository) |
| `xlink.xsd` | http://www.loc.gov/standards/xlink/xlink.xsd | Library of Congress, public |
| `page-2019-07-15.xsd` | https://www.primaresearch.org/schema/PAGE/gts/pagecontent/2019-07-15/pagecontent.xsd | Apache-2.0 (PRImA Research Lab) |
| `iiif_3_0.json` | https://raw.githubusercontent.com/IIIF/presentation-validator/main/schema/iiif_3_0.json (JSON Schema used by the official IIIF Presentation validator) | Apache-2.0 (IIIF) |
| `tei_all.rng` | https://tei-c.org/release/xml/tei/custom/schema/relaxng/tei_all.rng — TEI P5 4.12.0 | CC-BY 3.0 / BSD-2-Clause (TEI Consortium) |

Downloaded 2026-11-18; checksums in `SHA256SUMS`.
