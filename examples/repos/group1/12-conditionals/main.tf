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
  out = trimsuffix(var.output_dir, "/")

  log_level   = var.enable_debug ? "DEBUG" : "WARNING"
  worker_type = var.environment == "production" ? "gunicorn" : "uvicorn"
  replicas    = var.environment == "production" ? 3 : 1
}

# ------------------------------------------------------------------
# Debug config — only created when enable_debug = true
# ------------------------------------------------------------------
resource "local_file" "debug_config" {
  count = var.enable_debug ? 1 : 0

  filename        = "${local.out}/debug.yaml"
  file_permission = "0644"
  content         = <<-YAML
    # Debug configuration (only present in debug mode)
    debug: true
    log_level: DEBUG
    profiler: enabled
    sql_echo: true
    stack_traces: verbose
  YAML
}

# ------------------------------------------------------------------
# Admin secret — only generated when create_admin = true
# ------------------------------------------------------------------
resource "random_password" "admin_secret" {
  count  = var.create_admin ? 1 : 0
  length = 32
  special = false
}

resource "local_sensitive_file" "admin_env" {
  count = var.create_admin ? 1 : 0

  filename        = "${local.out}/.admin.env"
  file_permission = "0600"
  content         = "ADMIN_SECRET=${random_password.admin_secret[0].result}\n"
}

# ------------------------------------------------------------------
# One env file per enabled environment (for_each on filtered set)
# ------------------------------------------------------------------
resource "local_file" "env_files" {
  for_each = toset(var.enabled_envs)

  filename        = "${local.out}/.env.${each.key}"
  file_permission = "0644"
  content         = <<-ENV
    APP_ENV=${each.key}
    LOG_LEVEL=${local.log_level}
    WORKER=${local.worker_type}
    REPLICAS=${local.replicas}
  ENV
}

# ------------------------------------------------------------------
# Summary: always created — shows what was (or was not) created
# ------------------------------------------------------------------
resource "local_file" "summary" {
  filename        = "${local.out}/SUMMARY.txt"
  file_permission = "0644"
  content         = <<-TXT
    Conditionals demo summary
    =========================
    environment  : ${var.environment}
    enable_debug : ${var.enable_debug}
    create_admin : ${var.create_admin}
    enabled_envs : ${join(", ", var.enabled_envs)}

    debug_config : ${var.enable_debug ? "${local.out}/debug.yaml (created)" : "(skipped)"}
    admin_env    : ${var.create_admin ? "${local.out}/.admin.env (created)" : "(skipped)"}
  TXT
}
