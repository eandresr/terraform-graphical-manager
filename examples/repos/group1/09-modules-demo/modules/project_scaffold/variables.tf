variable "project_dir" {
  description = "Absolute path to the directory that will be scaffolded."
  type        = string
}

variable "project_name" {
  description = "Human-readable project name."
  type        = string
}

variable "language" {
  description = "Primary programming language (affects .gitignore patterns)."
  type        = string
  default     = "generic"
}

variable "description" {
  description = "Short one-line project description."
  type        = string
  default     = "A project scaffolded by Terraform."
}
