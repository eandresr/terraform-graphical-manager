variable "output_dir" {
  description = "Directory where all demo files will be written."
  type        = string
  default     = "/tmp/tf-lifecycle"
}

variable "server_generation" {
  description = "Increment this to trigger a new pet name (create_before_destroy demo)."
  type        = number
  default     = 1
}

variable "app_version" {
  description = "Application version string.  Changing it triggers replace_triggered_by."
  type        = string
  default     = "1.0.0"
}
