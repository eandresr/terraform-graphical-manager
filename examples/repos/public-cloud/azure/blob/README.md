# Azure — Blob Storage Container

Creates a Resource Group, a Storage Account and a private Blob Container.

---

## Workspace variables (`terraform` type → injected as `TF_VAR_*`)

These three are shared across all Azure workspaces and should be declared at workspace level.

| Variable | Required | Default | Example value |
|---|---|---|---|
| `subscription_id` | ✅ | — | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| `tenant_id` | ✅ | — | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| `client_id` | ✅ | — | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| `resource_group_name` | ✅ | — | `rg-myapp-prod` |
| `storage_account_name` | ✅ | — | `myappstorprod` (3-24 chars, lowercase alphanumeric) |
| `location` | ❌ | `westeurope` | `northeurope`, `eastus` |
| `container_name` | ❌ | `data` | `backups` |

---

## Environment variables (`env` type → credentials)

| Variable | Required | Description |
|---|---|---|
| `ARM_CLIENT_SECRET` | ✅ | Client secret of the Service Principal |

> The Service Principal (`client_id`) needs at minimum the `Contributor` role scoped to the subscription or target resource group.
