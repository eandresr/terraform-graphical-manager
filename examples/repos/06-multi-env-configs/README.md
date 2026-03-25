# 06 · Multi-Env Configs

Creates one `.env` configuration file per environment (dev/staging/prod)
with environment-specific values populated from variables.
Uses `hashicorp/local` only.

## Resources created
| Resource | Description |
|---|---|
| `local_file.env[*]` | One `.env` file per environment |
| `local_file.summary` | Plain-text summary listing all generated files |

## Usage
```bash
terraform init
terraform apply
ls /tmp/tf-envs/
```
