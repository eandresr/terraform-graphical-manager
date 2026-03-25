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

# ------------------------------------------------------------------
# Call the same module twice with different configs
# ------------------------------------------------------------------
module "backend_service" {
  source      = "./modules/project_scaffold"
  project_dir = "${var.base_dir}/backend-service"
  project_name = "backend-service"
  language    = "python"
  description = "REST API service built with FastAPI."
}

module "frontend_app" {
  source       = "./modules/project_scaffold"
  project_dir  = "${var.base_dir}/frontend-app"
  project_name = "frontend-app"
  language     = "typescript"
  description  = "Single-page application built with React."
}
