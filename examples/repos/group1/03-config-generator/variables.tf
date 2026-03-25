variable "output_dir" {
  description = "Directory where generated config files will be saved."
  type        = string
  default     = "/tmp/tf-app-config"
}

variable "app_name" {
  description = "Name of the application (used in config values and filenames)."
  type        = string
  default     = "myapp"
}

variable "app_env" {
  description = "Deployment environment: development, staging, or production."
  type        = string
  default     = "development"

  validation {
    condition     = contains(["development", "staging", "production"], var.app_env)
    error_message = "app_env must be one of: development, staging, production."
  }
}
