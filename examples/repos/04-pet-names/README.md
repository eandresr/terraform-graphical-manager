# 04 · Pet Names

Uses `random_pet` to generate human-readable environment and service names.
Great for ephemeral environment naming — no cloud provider required.

## Resources created
| Resource | Description |
|---|---|
| `random_pet.environment` | Human-readable environment name (e.g. `lively-falcon`) |
| `random_pet.services[*]` | One pet-name per service in `var.services` |
| `local_file.names_registry` | JSON file mapping services to their generated names |

## Usage
```bash
terraform init
terraform apply
terraform output -json service_names
```
