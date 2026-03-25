# 08 · Null Resources & Triggers

Demonstrates `null_resource` with local-exec provisioners and `terraform_data`
triggers.  Runs shell commands locally (echo, date, env).
Requires no cloud provider — only the `hashicorp/null` provider.

## Resources created
| Resource | Description |
|---|---|
| `null_resource.banner` | Prints a startup banner via local-exec |
| `null_resource.timestamp` | Writes current timestamp to a file |
| `terraform_data.trigger_demo` | Re-runs when `var.message` changes |

## Usage
```bash
terraform init
terraform apply
terraform apply -var="message=hello-world"   # triggers re-run
```
