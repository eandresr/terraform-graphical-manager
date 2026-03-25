terraform {
  required_version = ">= 1.3.0"

  required_providers {
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }
}

locals {
  out = trimsuffix(var.output_dir, "/")

  # ── Basic for expression: transform list ──────────────────────────
  uppercased_tags = [for t in var.tags : upper(t)]

  # ── Filter: only tags that start with "env-" ─────────────────────
  env_tags = [for t in var.tags : t if startswith(t, "env-")]

  # ── Map from list: tag => length(tag) ────────────────────────────
  tag_lengths = { for t in var.tags : t => length(t) }

  # ── zipmap: pair two lists into a map ────────────────────────────
  service_port_map = zipmap(var.services, var.service_ports)

  # ── merge: defaults overridden by custom settings ────────────────
  default_settings = {
    timeout     = 30
    max_retries = 3
    log_level   = "INFO"
    debug       = false
  }
  final_settings = merge(local.default_settings, var.custom_settings)

  # ── setproduct: all (env, service) pairs ─────────────────────────
  env_service_pairs = [
    for pair in setproduct(var.environments, var.services) :
    { env = pair[0], service = pair[1], key = "${pair[0]}-${pair[1]}" }
  ]

  # ── flatten: nested list → flat list ─────────────────────────────
  all_hostnames = flatten([
    for env in var.environments : [
      for svc in var.services :
      "${svc}.${env}.internal"
    ]
  ])

  # ── regexall: extract words from a sentence ───────────────────────
  extracted_words = regexall("[A-Za-z]+", var.sentence)
}

# ------------------------------------------------------------------
# Write report JSON
# ------------------------------------------------------------------
resource "local_file" "report" {
  filename        = "${local.out}/report.json"
  file_permission = "0644"
  content = jsonencode({
    uppercased_tags    = local.uppercased_tags
    env_tags           = local.env_tags
    tag_lengths        = local.tag_lengths
    service_port_map   = local.service_port_map
    final_settings     = local.final_settings
    env_service_pairs  = local.env_service_pairs
    all_hostnames      = local.all_hostnames
    extracted_words    = local.extracted_words
  })
}
