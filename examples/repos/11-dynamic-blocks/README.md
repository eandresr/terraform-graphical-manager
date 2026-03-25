# 11 · Dynamic Blocks

Demonstrates the `dynamic` block feature and `for_each` / `for` expressions
using only the `hashicorp/local` provider.

Generates an Nginx-style virtual host config file with a dynamic number of
`location` blocks built from a variable.

## Resources created
| Resource | Description |
|---|---|
| `local_file.nginx_conf` | Nginx-style vhost config with dynamic location blocks |
| `local_file.haproxy_conf` | HAProxy-style backend config with dynamic server entries |

## Usage
```bash
terraform init
terraform apply
cat /tmp/tf-dynamic/nginx.conf
```
