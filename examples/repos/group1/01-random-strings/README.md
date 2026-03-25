# 01 · Random Strings

Generates a set of random strings of configurable length and character sets.
No cloud provider is required — only the `hashicorp/random` provider.

## Resources created
| Resource | Description |
|---|---|
| `random_string.app_suffix` | Short alphanumeric identifier appended to resource names |
| `random_string.session_key` | 64-char mixed-case session key |
| `random_password.db_password` | 24-char password with special characters |
| `random_id.correlation` | 16-byte random ID encoded as hex |

## Usage
```bash
terraform init
terraform apply
terraform output
```
