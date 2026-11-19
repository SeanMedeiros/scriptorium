# REST API

The complete, machine-readable contract is the OpenAPI 3.1 document:

* live: `GET /api/openapi.json`, interactive explorers at `/api/docs` (Swagger UI) and `/api/redoc`
  (both self-hosted, no CDN);
* snapshot of this release: [`docs/api/openapi.json`](api/openapi.json) (133 operations, 109 paths).

## Conventions

* Base path `/api/v1`; JSON request and response bodies; UTF-8 (transcriptions keep their Unicode
  normalisation byte for byte).
* **Authentication**: `POST /api/v1/auth/login` returns a 15-minute bearer access token and sets an
  HttpOnly refresh cookie (`POST /api/v1/auth/refresh` rotates it). Send
  `Authorization: Bearer <token>` on every other call.
* **Authorization**: role-based per institution and project. Resources you may not see answer **404**
  (their existence is not disclosed); visible resources you may not modify answer **403**.
* **Errors** are structured:
  ```json
  {"error": {"code": "malware_detected", "message": "…", "details": {…}, "request_id": "…"}}
  ```
  `code` is stable and documented per endpoint; `request_id` matches the `X-Request-ID` response
  header and the server logs.
* **Pagination**: list endpoints take `limit`/`offset` and return `{total, limit, offset, items}`.
* **Long-running work** (ingestion, recognition, model installation, training, exports) is queued:
  the call returns the job/export resource immediately; poll it for `status`.
* **Concurrency**: transcription edits carry `expected_version_id` per line; a stale edit answers
  409 `edit_conflict` instead of overwriting another researcher's work.
* **Validation headers**: XML/IIIF exports return `X-Scriptorium-Validation` (JSON: schema, valid,
  errors) next to the file.

## Example: from upload to TEI

```bash
API=https://scriptorium.example.org/api/v1
TOKEN=$(curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
        -d '{"email":"me@uni.edu","password":"…"}' | jq -r .access_token)
H="Authorization: Bearer $TOKEN"
DOC=$(curl -s -H "$H" -F files=@folio-12r.jpg -F 'metadata={"languages":["por"]}' \
        $API/projects/$P/collections/$C/documents | jq -r '.[0].id')
PAGE=$(curl -s -H "$H" $API/documents/$DOC | jq -r '.pages[0].id')
JOB=$(curl -s -H "$H" -H 'Content-Type: application/json' \
     -d "{\"page_ids\":[\"$PAGE\"],\"recognition_model_version_id\":\"$MV\"}" $API/projects/$P/recognition-jobs | jq -r '.jobs[0].id')
# … poll until "status" is "completed":
REC=$(curl -s -H "$H" $API/recognition-jobs/$JOB | jq -r .output_recognition_id)
TX=$(curl -s -H "$H" -X POST $API/pages/$PAGE/transcriptions -H 'Content-Type: application/json' \
     -d "{\"recognition_id\":\"$REC\"}" | jq -r .id)
curl -s -H "$H" "$API/transcriptions/$TX/export?format=tei" -o folio-12r.tei.xml
```

(Field names and query parameters for every call are in the OpenAPI document.)

## Operations by area

### Authentication (7)

* `POST /api/v1/auth/register`
* `POST /api/v1/auth/login`
* `POST /api/v1/auth/refresh`
* `POST /api/v1/auth/logout`
* `POST /api/v1/auth/password-reset/request`
* `POST /api/v1/auth/password-reset/confirm`
* `POST /api/v1/auth/password-change`

### Users (6)

* `GET /api/v1/me`
* `PATCH /api/v1/me`
* `GET /api/v1/me/sessions`
* `DELETE /api/v1/me/sessions/{family_id}`
* `GET /api/v1/admin/users`
* `PATCH /api/v1/admin/users/{user_id}`

### Institutions (8)

* `GET /api/v1/institutions`
* `POST /api/v1/institutions`
* `GET /api/v1/institutions/{institution_id}`
* `GET /api/v1/institutions/{institution_id}/members`
* `PUT /api/v1/institutions/{institution_id}/members`
* `DELETE /api/v1/institutions/{institution_id}/members/{user_id}`
* `GET /api/v1/institutions/{institution_id}/storage`
* `PUT /api/v1/institutions/{institution_id}/storage-policy`

### Projects (13)

* `GET /api/v1/projects`
* `POST /api/v1/projects`
* `GET /api/v1/projects/{project_id}`
* `PATCH /api/v1/projects/{project_id}`
* `DELETE /api/v1/projects/{project_id}`
* `GET /api/v1/projects/{project_id}/members`
* `PUT /api/v1/projects/{project_id}/members`
* `DELETE /api/v1/projects/{project_id}/members/{user_id}`
* `GET /api/v1/projects/{project_id}/collections`
* `POST /api/v1/projects/{project_id}/collections`
* `GET /api/v1/projects/{project_id}/collections/{collection_id}`
* `PATCH /api/v1/projects/{project_id}/collections/{collection_id}`
* `DELETE /api/v1/projects/{project_id}/collections/{collection_id}`

### Documents (9)

* `POST /api/v1/projects/{project_id}/collections/{collection_id}/documents`
* `GET /api/v1/projects/{project_id}/collections/{collection_id}/documents`
* `GET /api/v1/documents/{document_id}`
* `PATCH /api/v1/documents/{document_id}`
* `DELETE /api/v1/documents/{document_id}`
* `GET /api/v1/pages/{page_id}`
* `GET /api/v1/documents/{document_id}/original`
* `GET /api/v1/assets/{asset_id}/content`
* `POST /api/v1/documents/{document_id}/reingest`

### Recognition (11)

* `GET /api/v1/models`
* `GET /api/v1/models/{model_version_id}`
* `POST /api/v1/projects/{project_id}/recognition-jobs`
* `GET /api/v1/projects/{project_id}/recognition-jobs`
* `GET /api/v1/recognition-jobs/{job_id}`
* `POST /api/v1/recognition-jobs/{job_id}/cancel`
* `POST /api/v1/recognition-jobs/{job_id}/retry`
* `GET /api/v1/pages/{page_id}/segmentations`
* `GET /api/v1/segmentations/{segmentation_id}`
* `GET /api/v1/pages/{page_id}/recognitions`
* `GET /api/v1/recognitions/{recognition_id}`

### Transcriptions (14)

* `POST /api/v1/pages/{page_id}/segmentations`
* `GET /api/v1/pages/{page_id}/transcriptions`
* `POST /api/v1/pages/{page_id}/transcriptions`
* `GET /api/v1/transcriptions/{transcription_id}`
* `POST /api/v1/transcriptions/{transcription_id}/revisions`
* `GET /api/v1/transcriptions/{transcription_id}/revisions`
* `GET /api/v1/transcriptions/{transcription_id}/lines/{line_id}/history`
* `POST /api/v1/transcriptions/{transcription_id}/lines/{line_id}/restore`
* `PUT /api/v1/transcriptions/{transcription_id}/policy`
* `PUT /api/v1/projects/{project_id}/transcription-policy`
* `POST /api/v1/editorial/validate`
* `GET /api/v1/transcriptions/{transcription_id}/annotations`
* `POST /api/v1/transcriptions/{transcription_id}/annotations`
* `POST /api/v1/annotations/{annotation_id}/resolve`

### Model registry (12)

* `GET /api/v1/model-registry/models`
* `GET /api/v1/model-registry/recommend`
* `GET /api/v1/admin/model-registry/catalog`
* `GET /api/v1/admin/model-registry/preview`
* `GET /api/v1/admin/model-registry/discover`
* `POST /api/v1/admin/model-registry/install`
* `POST /api/v1/admin/model-versions/{version_id}/retry`
* `POST /api/v1/admin/model-versions/{version_id}/revalidate`
* `POST /api/v1/admin/models/{model_id}/active-version`
* `POST /api/v1/admin/models/{model_id}/check-updates`
* `PATCH /api/v1/admin/models/{model_id}/curation`
* `GET /api/v1/admin/model-versions/{version_id}`

### Evaluation (14)

* `GET /api/v1/evaluation/methodology`
* `GET /api/v1/projects/{project_id}/evaluation-datasets`
* `POST /api/v1/projects/{project_id}/evaluation-datasets`
* `GET /api/v1/evaluation-datasets/{dataset_id}`
* `POST /api/v1/evaluation-datasets/{dataset_id}/ground-truth/from-transcription`
* `POST /api/v1/evaluation-datasets/{dataset_id}/ground-truth/import`
* `GET /api/v1/ground-truth/{gt_id}`
* `POST /api/v1/ground-truth/{gt_id}/withdraw`
* `POST /api/v1/evaluation-datasets/{dataset_id}/runs`
* `GET /api/v1/evaluation-datasets/{dataset_id}/runs`
* `GET /api/v1/evaluation-runs/{run_id}`
* `GET /api/v1/projects/{project_id}/evaluation/results`
* `GET /api/v1/evaluation-results/{result_id}`
* `POST /api/v1/evaluation/score`

### Comparison (4)

* `POST /api/v1/pages/{page_id}/comparisons`
* `GET /api/v1/pages/{page_id}/comparisons`
* `GET /api/v1/comparisons/{comparison_id}`
* `POST /api/v1/comparisons/{comparison_id}/combine`

### Exports (8)

* `GET /api/v1/transcriptions/{transcription_id}/export`
* `GET /api/v1/recognitions/{recognition_id}/export`
* `GET /api/v1/transcriptions/{transcription_id}/provenance`
* `POST /api/v1/projects/{project_id}/exports`
* `GET /api/v1/projects/{project_id}/exports`
* `GET /api/v1/exports/{export_id}`
* `GET /api/v1/exports/{export_id}/download`
* `POST /api/v1/evaluation-datasets/{dataset_id}/training-data-export`

### Reports (2)

* `GET /api/v1/projects/{project_id}/report`
* `GET /api/v1/institutions/{institution_id}/report`

### Training (15)

* `GET /api/v1/projects/{project_id}/training-jobs`
* `POST /api/v1/projects/{project_id}/training-jobs`
* `GET /api/v1/training-jobs/{job_id}`
* `POST /api/v1/training-jobs/{job_id}/cancel`
* `DELETE /api/v1/training-jobs/{job_id}/checkpoints/{name}`
* `POST /api/v1/training-jobs/{job_id}/checkpoints/{name}/promote`
* `GET /api/v1/projects/{project_id}/institutional-models`
* `POST /api/v1/projects/{project_id}/institutional-models`
* `GET /api/v1/institutional-models/{mv_id}`
* `POST /api/v1/institutional-models/{mv_id}/activate`
* `GET /api/v1/institutional-models/{mv_id}/download`
* `POST /api/v1/institutional-models/{mv_id}/publications`
* `POST /api/v1/model-publications/{pub_id}/approve`
* `GET /api/v1/model-publications/{pub_id}/package`
* `POST /api/v1/model-publications/{pub_id}/published`

### Interoperability (4)

* `GET /api/v1/projects/{project_id}/annotations`
* `GET /api/v1/projects/{project_id}/annotations/w3c`
* `GET /api/v1/projects/{project_id}/analytics`
* `GET /api/v1/documents/{document_id}/iiif/manifest.json`

### Dashboard (2)

* `GET /api/v1/dashboard`
* `GET /api/v1/audit-logs`

### Administration (2)

* `POST /api/v1/admin/retention`
* `GET /api/v1/admin/security-status`

### Health (2)

* `GET /api/health/live`
* `GET /api/health/ready`

