# GCP — Cloud Storage Bucket

Creates a GCS bucket with versioning and uniform bucket-level access enabled.

---

## Workspace variables (`terraform` type → injected as `TF_VAR_*`)

| Variable | Required | Default | Example value |
|---|---|---|---|
| `project_id` | ✅ | — | `my-gcp-project-123` |
| `bucket_name` | ✅ | — | `my-app-state-prod` |
| `location` | ❌ | `EU` | `europe-west1`, `EUR4`, `US` |
| `region` | ❌ | `europe-west1` | `us-central1` |

---

## Environment variables (`env` type → credentials)

| Variable | Required | Description |
|---|---|---|
| `TF_VAR_credentials_json` | ✅ | Service account key JSON as a **single-line** string |

> To convert a downloaded JSON key file to a single line:
> ```
> cat key.json | tr -d '\n'
> ```
>
> The service account needs at minimum the `roles/storage.admin` role (or `roles/storage.objectAdmin` + `roles/storage.legacyBucketOwner`).
