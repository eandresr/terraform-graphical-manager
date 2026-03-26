# Sentinel Policy Examples

Colección de políticas Sentinel de ejemplo listas para usar con **Terraform Graphical Manager**.  
Copia uno o varios policy sets en tu directorio de políticas globales o por workspace.

---

## Estructura

```
examples/sentinel/
├── security/          ← Seguridad: S3, IAM, TLS, secretos
├── tagging/           ← Etiquetado corporativo obligatorio
├── cost/              ← Control de costes: tipos de instancia
└── networking/        ← Red: puertos abiertos, IPs públicas, VPC
```

Cada subdirectorio es un **policy set** independiente.  
Contiene uno o más archivos `.sentinel` y un `sentinel.hcl` opcional para declarar niveles de enforcement por política.

---

## Policy sets

### `security/` — Seguridad

| Política | Nivel | Qué comprueba |
|---|---|---|
| `s3-no-public-acl` | hard-mandatory | Ningún bucket S3 con ACL pública |
| `s3-encryption-enabled` | hard-mandatory | SSE obligatoria en todos los buckets S3 |
| `iam-no-wildcard-actions` | hard-mandatory | Sin `Action: "*"` en políticas IAM |
| `iam-no-admin-policy` | hard-mandatory | No adjuntar `AdministratorAccess` |
| `tls-minimum-version` | soft-mandatory | TLS ≥ 1.2 en ALB y CloudFront |
| `secrets-not-in-variables` | hard-mandatory | Variables con nombre de secreto marcadas como `sensitive` |

---

### `tagging/` — Etiquetado

| Política | Nivel | Qué comprueba |
|---|---|---|
| `required-tags` | hard-mandatory | Tags obligatorias: `Name`, `Environment`, `Owner`, `CostCenter` |
| `environment-tag-valid-values` | soft-mandatory | `Environment` ∈ {dev, staging, prod, sandbox} |
| `no-default-tags-override` | advisory | Avisa si se sobreescriben tags corporativas manualmente |

---

### `cost/` — Control de costes

| Política | Nivel | Qué comprueba |
|---|---|---|
| `allowed-instance-types` | soft-mandatory | EC2 limitado al catálogo aprobado |
| `no-expensive-instance-types` | hard-mandatory | Bloquea familias p, x, u (GPU / memoria extrema) |
| `rds-no-multi-az-in-dev` | advisory | Avisa si RDS Multi-AZ en entorno no-prod |
| `ec2-ebs-optimized-required` | advisory | Aconseja `ebs_optimized = true` en familias antiguas |

---

### `networking/` — Seguridad de red

| Política | Nivel | Qué comprueba |
|---|---|---|
| `no-ssh-open-to-world` | hard-mandatory | Puerto 22 no abierto a 0.0.0.0/0 |
| `no-rdp-open-to-world` | hard-mandatory | Puerto 3389 no abierto a 0.0.0.0/0 |
| `no-unrestricted-ingress` | soft-mandatory | Solo 80/443 pueden estar abiertos al mundo |
| `no-public-ip-on-launch` | soft-mandatory | Sin IPs públicas directas en subnets/instancias |
| `vpc-flow-logs-enabled` | soft-mandatory | Toda VPC nueva debe tener flow logs |

---

## Niveles de enforcement

| Nivel | Efecto |
|---|---|
| `hard-mandatory` | La política **debe** pasar; el apply se bloquea si falla |
| `soft-mandatory` | El fallo bloquea por defecto, pero puede omitirse con justificación |
| `advisory` | Solo genera un aviso; nunca bloquea |

---

## Uso rápido

1. Configura el directorio de políticas globales en Settings:

   ```ini
   [sentinel]
   global_policies = /ruta/a/examples/sentinel
   enforce_on_plan = true
   enforce_on_apply = true
   ```

2. O apunta a un policy set individual para probar antes de aplicar globalmente.

3. Ajusta los valores hardcodeados de cada política a tu organización:
   - `required_tags` en `tagging/required-tags.sentinel`
   - `allowed_instance_types` en `cost/allowed-instance-types.sentinel`
   - `allowed_environments` en `tagging/environment-tag-valid-values.sentinel`

---

## Referencia del mock `tfplan/v2`

Todas las políticas importan `tfplan/v2`.  
TGM genera automáticamente el mock al ejecutar un `sentinel apply`, por lo que no necesitas configuración adicional más allá de apuntar al directorio correcto.

La estructura del mock sigue el
[esquema oficial de Terraform Cloud tfplan/v2](https://developer.hashicorp.com/terraform/cloud-docs/policy-enforcement/sentinel/import/tfplan-v2).
