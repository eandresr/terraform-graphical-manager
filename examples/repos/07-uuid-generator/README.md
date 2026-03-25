# 07 · UUID Generator

Generates UUIDs suitable for use as resource identifiers, correlation IDs,
or database primary keys.  Uses only `hashicorp/random` + `hashicorp/local`.

## Resources created
| Resource | Description |
|---|---|
| `random_uuid.ids[*]` | One UUID v4 per entry in `var.id_names` |
| `local_file.uuid_manifest` | JSON manifest of all generated UUIDs |

## Usage
```bash
terraform init
terraform apply
terraform output -json uuid_map
```
