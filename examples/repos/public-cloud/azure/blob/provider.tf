terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

# Authentication uses a Service Principal with client secret.
# The secret is read natively from the environment variable:
#   ARM_CLIENT_SECRET
#
# tenant_id, subscription_id and client_id are passed as Terraform variables
# (declared at workspace level in the app).
provider "azurerm" {
  features {}

  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id
  client_id       = var.client_id
}
