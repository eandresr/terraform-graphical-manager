# AWS — S3 Bucket

Creates an S3 bucket with versioning, AES-256 server-side encryption and public access fully blocked.

---

## Workspace variables (`terraform` type → injected as `TF_VAR_*`)

| Variable | Required | Default | Example value |
|---|---|---|---|
| `bucket_name` | ✅ | — | `my-app-state-prod` |
| `region` | ❌ | `eu-west-1` | `us-east-1` |

---

## Environment variables (`env` type → credentials)

| Variable | Required | Description |
|---|---|---|
| `AWS_ACCESS_KEY_ID` | ✅ | Access key ID of the IAM user |
| `AWS_SECRET_ACCESS_KEY` | ✅ | Secret access key of the IAM user |

> The IAM user needs at minimum the `s3:CreateBucket`, `s3:PutBucketVersioning`, `s3:PutEncryptionConfiguration` and `s3:PutBucketPublicAccessBlock` permissions.
