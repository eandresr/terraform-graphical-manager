terraform {
  required_version = ">= 1.3.0"

  required_providers {
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

locals {
  out       = trimsuffix(var.output_dir, "/")
  seed_path = "${local.out}/seed.json"
}

# ------------------------------------------------------------------
# 1. Write a seed JSON file
# ------------------------------------------------------------------
resource "local_file" "seed" {
  filename        = local.seed_path
  file_permission = "0644"
  content = jsonencode({
    items   = var.items
    version = var.seed_version
  })
}

# ------------------------------------------------------------------
# 2. Read the seed file back via a data source
# ------------------------------------------------------------------
data "local_file" "seed_data" {
  filename   = local_file.seed.filename
  depends_on = [local_file.seed]
}

# ------------------------------------------------------------------
# 3. Shuffle the items list (re-shuffles when seed version changes)
# ------------------------------------------------------------------
resource "random_shuffle" "order" {
  input        = var.items
  result_count = length(var.items)
  keepers = {
    version = var.seed_version
  }
}

# ------------------------------------------------------------------
# 4. Write a processed file that uses data from the data source
# ------------------------------------------------------------------
resource "local_file" "processed" {
  filename        = "${local.out}/processed.txt"
  file_permission = "0644"
  content = join("\n", concat(
    [
      "# Processed output",
      "# Source: ${data.local_file.seed_data.filename}",
      "# Seed version: ${var.seed_version}",
      "",
      "## Original order:",
    ],
    [for i, item in var.items : "  ${i + 1}. ${item}"],
    ["", "## Shuffled order:"],
    [for i, item in random_shuffle.order.result : "  ${i + 1}. ${item}"],
    [""]
  ))
}
