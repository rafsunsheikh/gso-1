---
name: docker
description: Containers and compose stacks: what is running, why one exited, disk reclaimed. Use when asked about containers, images, a compose stack, or Docker eating disk space.
requires: [docker]
---

# Docker

## What is running, and what died

```bash
docker ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}'
docker ps -a --filter status=exited --format '{{.Names}}\t{{.Status}}'
docker logs --tail 60 <name>
```

An exited container's status carries its exit code: `Exited (137)` is a kill,
usually the out-of-memory killer, and `Exited (0)` finished normally.

## Compose stacks

Run from the directory holding the compose file.

```bash
docker compose ps
docker compose logs --tail 60 <service>
docker compose config --services
```

## Disk

Docker is a common cause of a suddenly full disk, and the answer is usually
build cache rather than images.

```bash
docker system df
```

Report what it says. Do not run `docker system prune` unless the operator asks
for it in this conversation: it deletes build caches and volumes, and a volume
can hold the only copy of a database.

## What not to do

- Never `docker rm`, `docker rmi`, `prune`, or stop a running container unless
  asked. Report and let the operator decide.
- Prefer `docker compose` over the older `docker-compose` when both exist.
