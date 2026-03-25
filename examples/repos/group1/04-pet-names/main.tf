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

# ------------------------------------------------------------------
# One pet name for the whole environment (e.g. "bright-osprey")
# ------------------------------------------------------------------
resource "random_pet" "environment" {
  length    = var.pet_word_count
  separator = var.separator
  prefix    = var.env_prefix != "" ? var.env_prefix : null
}

# ------------------------------------------------------------------
# One pet name per service
# ------------------------------------------------------------------
resource "random_pet" "services" {
  for_each  = toset(var.services)
  length    = 2
  separator = var.separator
  keepers = {
    service = each.key
  }
}

# ------------------------------------------------------------------
# Write registry to a local JSON file
# ------------------------------------------------------------------
resource "local_file" "names_registry" {
  filename        = "${var.output_dir}/names-registry.json"
  file_permission = "0644"
  content = jsonencode({
    environment = random_pet.environment.id
    services    = { for svc, r in random_pet.services : svc => r.id }
  })
}
