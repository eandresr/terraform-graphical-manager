terraform {
  required_version = ">= 1.3.0"

  required_providers {
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
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

resource "random_id" "run_id" {
  byte_length = 4
}

# ------------------------------------------------------------------
# Print a banner each time this resource is (re)created
# ------------------------------------------------------------------
resource "null_resource" "banner" {
  triggers = {
    always_run = plantimestamp()
  }

  provisioner "local-exec" {
    command = <<-SH
      echo "=================================================="
      echo "  Terraform Null Resource Demo"
      echo "  Run ID : ${random_id.run_id.hex}"
      echo "  Message: ${var.message}"
      echo "=================================================="
    SH
  }
}

# ------------------------------------------------------------------
# Write the current timestamp to a file (triggers on message change)
# ------------------------------------------------------------------
resource "null_resource" "timestamp" {
  triggers = {
    message = var.message
  }

  provisioner "local-exec" {
    command = "date -u '+%Y-%m-%dT%H:%M:%SZ' > '${local.out}/last_apply.txt' && echo 'Message: ${var.message}' >> '${local.out}/last_apply.txt'"
  }
}

# ------------------------------------------------------------------
# terraform_data replaces the old null_resource pattern for triggers only
# ------------------------------------------------------------------
resource "terraform_data" "trigger_demo" {
  input = var.message

  lifecycle {
    replace_triggered_by = [terraform_data.trigger_demo]
  }
}
