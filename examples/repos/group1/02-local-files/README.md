# 02 · Local Files

Creates a set of plain-text and JSON files on the local filesystem.
Only the `hashicorp/local` provider is required — no cloud connectivity.

## Resources created
| Resource | Description |
|---|---|
| `local_file.readme` | Project README placeholder |
| `local_file.gitignore` | `.gitignore` with common Terraform patterns |
| `local_file.env_example` | `.env.example` template |
| `local_file.makefile` | Simple `Makefile` with common targets |

## Usage
```bash
terraform init
terraform apply -var="output_dir=/tmp/my-project"
ls -la $(terraform output -raw output_dir)
```
