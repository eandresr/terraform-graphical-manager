variable "output_dir" {
  description = "Absolute or relative path where the generated files will be written."
  type        = string
  default     = "/tmp/tf-local-files"
}

variable "project_name" {
  description = "Name of the project — used inside generated file content."
  type        = string
  default     = "my-project"
}

variable "project_description" {
  description = "Short description inserted into the generated README."
  type        = string
  default     = "A project bootstrapped with Terraform."
}
