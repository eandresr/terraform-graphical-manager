# 09 · Modules Demo

Demonstrates Terraform **module** composition using only local providers.
A reusable `./modules/project_scaffold` module creates a full project
directory structure (README, .gitignore, Makefile, config.yaml).

## Structure
```
09-modules-demo/
├── main.tf          # Root module — calls project_scaffold twice
├── variables.tf
├── outputs.tf
└── modules/
    └── project_scaffold/
        ├── main.tf
        ├── variables.tf
        └── outputs.tf
```

## Usage
```bash
terraform init
terraform apply
```
