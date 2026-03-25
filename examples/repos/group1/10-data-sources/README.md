# 10 · Data Sources

Demonstrates reading local data sources: `local_file` data source, `random`
keeper patterns, and `terraform_remote_state` (local backend).
Only local providers required.

## Resources created
| Resource | Description |
|---|---|
| `local_file.seed` | Seed data file written first |
| `local_file.processed` | File whose content derives from the seed |
| `random_shuffle.order` | Shuffles a list read from the seed file |

## Concepts covered
- Data sources (`data "local_file"`)
- Lifecycle dependencies (`depends_on`)
- `random_shuffle` with `keepers`

## Usage
```bash
terraform init
terraform apply
```
