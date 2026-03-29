resource "google_storage_bucket" "this" {
  name          = var.bucket_name
  location      = var.location
  project       = var.project_id
  force_destroy = false

  uniform_bucket_level_access = true

  versioning {
    enabled = true
  }

  encryption {
    default_kms_key_name = null
  }
}

resource "google_storage_bucket_iam_binding" "public_access_block" {
  bucket = google_storage_bucket.this.name
  role   = "roles/storage.objectViewer"

  members = []
}
