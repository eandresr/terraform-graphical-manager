# 14 · Lifecycle Rules

Demonstrates `lifecycle` meta-arguments:
- `create_before_destroy`
- `prevent_destroy`
- `ignore_changes`
- `replace_triggered_by`

Uses only `hashicorp/local` + `hashicorp/random`.

## Resources in this workspace
| Resource | Lifecycle feature |
|---|---|
| `random_pet.server_name` | `create_before_destroy = true` |
| `local_file.immutable_config` | `prevent_destroy = true` (change in place only) |
| `local_file.ignored_content` | `ignore_changes = [content]` (drift ignored) |
| `terraform_data.version_trigger` | `replace_triggered_by` chain demo |

## Usage
```bash
terraform init
terraform apply

# Try to destroy the protected resource — Terraform will refuse:
terraform destroy -target=local_file.immutable_config
```
