variable "app_name" {
  description = "Application name used in config values."
  type        = string
  default     = "myapp"
}

variable "environments" {
  description = "List of environments to generate config files for."
  type        = list(string)
  default     = ["development", "staging", "production"]
}

variable "output_dir" {
  description = "Directory where .env.* files will be written."
  type        = string
  default     = "/tmp/tf-envs"
}
