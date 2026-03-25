output "nginx_conf_path" {
  description = "Path to the generated Nginx config."
  value       = local_file.nginx_conf.filename
}

output "haproxy_conf_path" {
  description = "Path to the generated HAProxy config."
  value       = local_file.haproxy_conf.filename
}
