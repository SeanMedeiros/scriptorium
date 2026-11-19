# Interoperability

| Exchange | Endpoint | Standard / validation |
|---|---|---|
| Transcription exports | `/transcriptions/{id}/export`, batch exports | TEI P5 (`tei_all` RNG), ALTO 4.4 (XSD), PAGE 2019 (XSD), hOCR (structural), TXT, JSON, DOCX, PDF |
| IIIF manifest | `/documents/{id}/iiif/manifest.json?layer=corrected\|htr` | IIIF Presentation 3.0; validated with the JSON Schema of the official IIIF presentation validator (vendored) before it is served |
| Annotations | `/projects/{id}/annotations/w3c` | W3C Web Annotation Data Model (JSON-LD, `AnnotationCollection`); line targets as `FragmentSelector` `xywh` on the page canvas |
| Training data | `/evaluation-datasets/{id}/training-data-export` | PAGE 2019 per page (validated), images, `train.txt`/`validation.txt`/`test.txt` — usable with `ketos train -f page` and importable in eScriptorium |
| Ground truth import | ALTO v2–v4, PAGE 2013/2019 | — |
| Models | Zenodo (htrmopo v0/v1 cards); upload `.mlmodel`/`.safetensors`; publication package with htrmopo v1 card + Zenodo metadata | htrmopo JSON schema |

## IIIF notes

* Canvas = the image the segmentation was computed on (dimensions given); painting annotation with a
  **signed, expiring** image URL; transcription lines as `supplementing` annotations with
  `TextualBody` targeting `canvas#xywh=…`.
* The manifest is intended for authenticated use and for exchanging structure and text. A public IIIF
  Image API service is not provided (images stay behind the platform's access control).

## W3C annotation mapping

| SCRIPTORIUM kind | W3C motivation |
|---|---|
| comment | `commenting` |
| query | `questioning` |
| palaeography | `describing` |

Resolved annotations carry `modified` and an extension property `status: "resolved"`.
