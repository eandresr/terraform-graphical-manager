# --- Workspace-level variables (set once per workspace in the app) ----------

variable "subscription_id" {
  description = "Azure Subscription ID."
  type        = string
}

variable "tenant_id" {
  description = "Azure AD Tenant ID."
  type        = string
}

variable "client_id" {
  description = "Service Principal Application (client) ID."
  type        = string
}

# --- Resource-level variables ------------------------------------------------

variable "location" {
  description = "Azure region where resources will be created."
  type        = string
  default     = "westeurope"
}

variable "resource_group_name" {
  description = "Name of the resource group."
  type        = string
}

variable "storage_account_name" {
  description = "Globally unique name for the storage account (3-24 chars, lowercase alphanumeric)."
  type        = string
}

variable "container_name" {
  description = "Name of the blob container."
  type        = string
  default     = "data"
}
