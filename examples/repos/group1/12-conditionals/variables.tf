variable "environment" {
  description = "Target environment name; affects worker type and replica count."
  type        = string
  default     = "development"
  validation {
    condition     = contains(["development", "staging", "production"], var.environment)
    error_message = "Must be development, staging, or production."
  }
}

variable "enable_debug" {
  description = "When true, a debug config file is created."
  type        = bool
  default     = true
}

variable "create_admin" {
  description = "When true, an admin secret is generated."
  type        = bool
  default     = false
}

variable "enabled_envs" {
  description = "Set of environment names for which .env files should be generated."
  type        = list(string)
  default     = ["development", "staging"]
}

variable "output_dir" {
  description = "Directory where output files will be written."
  type        = string
  default     = "/tmp/tf-conditionals"
}
