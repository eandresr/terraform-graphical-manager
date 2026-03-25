locals {
  dir = trimsuffix(var.project_dir, "/")

  gitignore_extra = {
    python     = "\n# Python\n__pycache__/\n*.py[cod]\n*.egg-info/\ndist/\nbuild/\n.venv/\n"
    typescript = "\n# Node / TypeScript\nnode_modules/\ndist/\nbuild/\n.cache/\n*.js.map\n"
    generic    = ""
  }
}

resource "local_file" "readme" {
  filename        = "${local.dir}/README.md"
  file_permission = "0644"
  content         = <<-EOF
    # ${var.project_name}

    ${var.description}

    ## Getting Started

    ```bash
    # Install dependencies
    make install

    # Run in development mode
    make dev
    ```

    ## Project Structure

    ```
    ${var.project_name}/
    ├── README.md
    ├── .gitignore
    ├── Makefile
    └── config.yaml
    ```
  EOF
}

resource "local_file" "gitignore" {
  filename        = "${local.dir}/.gitignore"
  file_permission = "0644"
  content         = <<-EOF
    # Terraform
    .terraform/
    *.tfstate
    *.tfstate.backup
    .terraform.lock.hcl

    # Environment
    .env
    .env.*
    !.env.example

    # OS
    .DS_Store
    Thumbs.db
    ${lookup(local.gitignore_extra, var.language, "")}
  EOF
}

resource "local_file" "makefile" {
  filename        = "${local.dir}/Makefile"
  file_permission = "0644"
  content         = <<-EOF
    .PHONY: all install dev test lint clean

    all: install

    install:
    	@echo "[${var.project_name}] Installing…"

    dev:
    	@echo "[${var.project_name}] Starting dev server…"

    test:
    	@echo "[${var.project_name}] Running tests…"

    lint:
    	@echo "[${var.project_name}] Linting…"

    clean:
    	@echo "[${var.project_name}] Cleaning…"
  EOF
}

resource "local_file" "config" {
  filename        = "${local.dir}/config.yaml"
  file_permission = "0644"
  content         = <<-EOF
    project:
      name: "${var.project_name}"
      language: "${var.language}"

    server:
      host: "0.0.0.0"
      port: 8080
  EOF
}
