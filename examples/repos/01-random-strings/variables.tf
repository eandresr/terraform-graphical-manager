variable "suffix_length" {
  description = "Length of the alphanumeric resource name suffix."
  type        = number
  default     = 8

  validation {
    condition     = var.suffix_length >= 4 && var.suffix_length <= 16
    error_message = "suffix_length must be between 4 and 16."
  }
}

variable "password_length" {
  description = "Length of the generated database password."
  type        = number
  default     = 24

  validation {
    condition     = var.password_length >= 16
    error_message = "password_length must be at least 16 characters."
  }
}
