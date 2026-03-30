terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

# Credentials are read from the environment variable:
#   GOOGLE_CREDENTIALS — GCP service account key JSON as a single-line string
#
# To convert a downloaded JSON key file to a single line:
#   cat key.json | tr -d '\n'
#
# The service account needs at minimum:
#   roles/storage.admin  (or roles/storage.objectAdmin + roles/storage.legacyBucketOwner)
provider "google" {
  project     = var.project_id
  region      = var.region
  credentials = var.credentials_json
}
