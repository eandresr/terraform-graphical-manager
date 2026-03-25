# 12 · Conditionals & Count

Demonstrates conditional resource creation via `count`, `for_each`, and
ternary expressions.  Uses `hashicorp/local` + `hashicorp/random`.

## Resources created (conditional)
| Resource | Condition |
|---|---|
| `local_file.debug_config` | Only when `var.enable_debug = true` |
| `random_password.admin_secret` | Only when `var.create_admin = true` |
| `local_file.env_files[*]` | One per enabled environment in `var.enabled_envs` |

## Usage
```bash
terraform init
# Default: debug on, admin off
terraform apply

# Flip both flags
terraform apply -var="enable_debug=false" -var="create_admin=true"
```
