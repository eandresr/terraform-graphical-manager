variable "message" {
  description = "Message printed by the banner and written to the timestamp file. Changing this re-triggers the null_resource."
  type        = string
  default     = "Hello from Terraform!"
}

variable "output_dir" {
  description = "Directory where local-exec output files will be written."
  type        = string
  default     = "/tmp/tf-null-demo"
}
