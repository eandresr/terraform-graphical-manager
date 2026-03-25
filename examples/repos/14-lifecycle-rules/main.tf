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
# create_before_destroy: new name is created before old one is removed.
# Useful when downstream references cannot tolerate a gap.
# ------------------------------------------------------------------
resource "random_pet" "server_name" {
  length    = 3
  separator = "-"
  keepers = {
    generation = var.server_generation
  }

  lifecycle {
    create_before_destroy = true
  }
}

# ------------------------------------------------------------------
# prevent_destroy: Terraform will refuse to destroy this resource.
# Useful for critical config files or certificates.
# ------------------------------------------------------------------
resource "local_file" "immutable_config" {
  filename        = "${local.out}/immutable.conf"
  file_permission = "0444" # read-only even at OS level

  content = <<-CONF
    # IMMUTABLE CONFIG — do not delete manually
    server_name = "${random_pet.server_name.id}"
    created_at  = "${plantimestamp()}"
  CONF

  lifecycle {
    prevent_destroy = true
  }
}

# ------------------------------------------------------------------
# ignore_changes: Terraform ignores content drift on this file.
# Simulates a file that is modified at runtime by the application.
# ------------------------------------------------------------------
resource "local_file" "ignored_content" {
  filename        = "${local.out}/runtime-state.json"
  file_permission = "0644"

  content = jsonencode({
    initialized = true
    note        = "This file may be modified at runtime; Terraform ignores content changes."
  })

  lifecycle {
    ignore_changes = [content]
  }
}

# ------------------------------------------------------------------
# replace_triggered_by: when the trigger data changes, the config
# resource is replaced (re-created).
# ------------------------------------------------------------------
resource "terraform_data" "version_trigger" {
  input = var.app_version
}

resource "local_file" "versioned_config" {
  filename        = "${local.out}/version.txt"
  file_permission = "0644"
  content         = "app_version=${var.app_version}\nserver=${random_pet.server_name.id}\n"

  lifecycle {
    replace_triggered_by = [terraform_data.version_trigger]
  }
}
