# 05 · Secret Tokens

Generates multiple random API tokens/keys and writes them to individually
permission-restricted files.  Uses only `hashicorp/random` + `hashicorp/local`.

## Resources created
| Resource | Description |
|---|---|
| `random_password.tokens[*]` | One 48-char token per entry in `var.token_names` |
| `local_sensitive_file.token_files[*]` | One file per token (mode 0600) |
| `local_file.token_index` | Plain-text index listing all token filenames |

## Usage
```bash
terraform init
terraform apply
terraform output -json token_file_paths
```
