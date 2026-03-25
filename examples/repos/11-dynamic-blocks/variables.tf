variable "server_name" {
  description = "The virtual host / service name."
  type        = string
  default     = "myapp.example.com"
}

variable "locations" {
  description = "Nginx location block definitions."
  type = list(object({
    path         = string
    backend_host = string
    backend_port = number
    timeout      = string
  }))
  default = [
    { path = "/api/v1/", backend_host = "127.0.0.1", backend_port = 8001, timeout = "60s" },
    { path = "/api/v2/", backend_host = "127.0.0.1", backend_port = 8002, timeout = "60s" },
    { path = "/static/", backend_host = "127.0.0.1", backend_port = 8080, timeout = "10s" },
    { path = "/ws/",     backend_host = "127.0.0.1", backend_port = 9000, timeout = "120s" },
  ]
}

variable "haproxy_servers" {
  description = "HAProxy backend server definitions."
  type = list(object({
    name   = string
    host   = string
    port   = number
    weight = number
  }))
  default = [
    { name = "web01", host = "10.0.0.1", port = 8080, weight = 10 },
    { name = "web02", host = "10.0.0.2", port = 8080, weight = 10 },
    { name = "web03", host = "10.0.0.3", port = 8080, weight = 5 },
  ]
}

variable "output_dir" {
  description = "Directory where generated config files will be written."
  type        = string
  default     = "/tmp/tf-dynamic"
}
