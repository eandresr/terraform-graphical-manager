terraform {
  required_version = ">= 1.3.0"

  required_providers {
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }
}

locals {
  out = trimsuffix(var.output_dir, "/")
}

# ------------------------------------------------------------------
# UUID v4 for each requested identifier
# ------------------------------------------------------------------
resource "random_uuid" "ids" {
  for_each = toset(var.id_names)
}

# ------------------------------------------------------------------
# Extra batch of UUIDs (fixed count)
# ------------------------------------------------------------------
resource "random_uuid" "batch" {
  count = var.batch_count
}

# ------------------------------------------------------------------
# JSON manifest of all UUIDs
# ------------------------------------------------------------------
resource "local_file" "uuid_manifest" {
  filename        = "${local.out}/uuids.json"
  file_permission = "0644"
  content = jsonencode({
    named = { for name, r in random_uuid.ids : name => r.result }
    batch = [for r in random_uuid.batch : r.result]
  })
}
