# 03 · Config Generator

Combines `random` and `local` providers to generate application configuration
files pre-filled with random secrets.  No cloud provider needed.

## Resources created
| Resource | Description |
|---|---|
| `random_password.secret_key` | Django/Flask-style SECRET_KEY |
| `random_password.jwt_secret` | JWT signing secret |
| `random_string.app_id` | Short application identifier |
| `local_file.app_config` | YAML application config file |
| `local_file.secrets_env` | `.env` file with all generated secrets |

## Usage
```bash
terraform init
terraform apply
cat $(terraform output -raw config_file_path)
```
