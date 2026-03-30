variable "credentials_json" {
  description = "GCP service account key as a single-line JSON string. Set via GOOGLE_CREDENTIALS env var (TF_VAR_credentials_json)."
  type        = string
  sensitive   = true
}

variable "project_id" {
  description = "GCP project ID where the bucket will be created."
  type        = string
}

variable "region" {
  description = "GCP region for the provider (does not affect bucket location)."
  type        = string
  default     = "europe-west1"
}

variable "location" {
  description = "Bucket location. Can be a region (europe-west1), dual-region (EUR4) or multi-region (EU, US)."
  type        = string
  default     = "EU"
}

variable "bucket_name" {
  description = "Globally unique name for the GCS bucket."
  type        = string
}
