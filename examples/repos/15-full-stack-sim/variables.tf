variable "project_name" {
  description = "Name of the project (used in config values and database name)."
  type        = string
  default     = "fullstack-demo"
}

variable "output_dir" {
  description = "Root directory where all generated files will be written."
  type        = string
  default     = "/tmp/tf-fullstack"
}
