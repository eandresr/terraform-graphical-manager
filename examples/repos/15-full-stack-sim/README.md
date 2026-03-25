# 15 · Full Stack Local Sim

A larger workspace that simulates a complete "local stack" bootstrap:
generates secrets, writes config files for three services, creates a
Docker Compose file, and produces a project index.

Uses `hashicorp/local` + `hashicorp/random` + `hashicorp/null`.

## Services simulated
| Service | Port | Description |
|---|---|---|
| api | random 8xxx | FastAPI backend |
| worker | random 8xxx | Background job worker |
| frontend | random 3xxx | React development server |

## Files produced
```
/tmp/tf-fullstack/
├── docker-compose.yml
├── .env.master
├── services/
│   ├── api/.env
│   ├── worker/.env
│   └── frontend/.env
└── INDEX.txt
```

## Usage
```bash
terraform init
terraform apply
cat /tmp/tf-fullstack/docker-compose.yml
```
