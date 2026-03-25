terraform {
  required_version = ">= 1.3.0"

  required_providers {
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

# ------------------------------------------------------------------
# Short suffix for naming resources (8 hex chars, lowercase)
# ------------------------------------------------------------------
resource "random_string" "app_suffix" {
  length  = var.suffix_length
  upper   = false
  special = false
}

# ------------------------------------------------------------------
# Session / CSRF key (64 chars, mixed alphanumeric)
# ------------------------------------------------------------------
resource "random_string" "session_key" {
  length  = 64
  upper   = true
  lower   = true
  numeric = true
  special = false
}

# ------------------------------------------------------------------
# Database password (with special chars)
# ------------------------------------------------------------------
resource "random_password" "db_password" {
  length           = var.password_length
  upper            = true
  lower            = true
  numeric          = true
  special          = true
  override_special = "!#$%&*()-_=+[]{}|;:,.<>?"
  min_upper        = 2
  min_lower        = 2
  min_numeric      = 2
  min_special      = 2
}

# ------------------------------------------------------------------
# Correlation / trace ID (hex-encoded random bytes)
# ------------------------------------------------------------------
resource "random_id" "correlation" {
  byte_length = 16
}
