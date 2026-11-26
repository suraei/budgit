# Mi fork de Budgit

Fork de [suraei/budgit](https://github.com/suraei/budgit) para personalizarlo.

## Cómo está montado

- `origin` = mi fork (`nilgaar/budgit`), `upstream` = el original (`suraei/budgit`).
- `main` es una copia limpia de upstream. **Nunca hago commits en `main`.**
- `custom` es mi rama de trabajo, con mis cambios encima de upstream.
- Claude Code carga el plugin **directamente desde esta carpeta** (marketplace local `budgit-hub`).
  Lo que esté en el checkout actual es lo que se ejecuta: si cambio de rama, cambia el plugin.
  Después de editar, `/reload-plugins` en Claude Code o abrir una sesión nueva.
- Config personal: `~/.budgit/config.json`. Hub privado: `~/Documents/budgit-hub`.
  Proyectos nuevos: `~/Documents/_repos`.

## Regla de oro: esta carpeta es el plugin en vivo

Aquí **no** se cambia de rama, ni se hace rebase, ni se resuelven conflictos: un conflicto dejaría marcas en el
código de los hooks y bloquearía todas las herramientas de Claude Code. Los guards de Budgit lo imponen a Claude
(aquí solo permite leer, `fetch`, `add`, `commit`, `worktree add` y `merge --ff-only`). Los cambios grandes se
preparan en un worktree aparte y se traen con fast-forward.

## Traer cambios del original

```bash
git fetch upstream
git push origin upstream/main:main
git worktree add ../budgit-next -b next custom
git -C ../budgit-next merge upstream/main
```

Reviso y paso los tests en `../budgit-next` (resolviendo ahí cualquier conflicto). Después:

```bash
git merge --ff-only next
git worktree remove ../budgit-next && git branch -d next
git push origin custom
```

## Cambiar algo del plugin

Para un cambio pequeño basta con editar aquí y hacer commit en `custom`. Para algo más grande, uso el mismo patrón:
`git worktree add ../budgit-wip -b wip custom`, trabajo y paso los tests allí, y luego `git merge --ff-only wip` aquí.

Budgit avisa una vez al día cuando sale una versión nueva en upstream (lee el `homepage` de `plugin.json`).

## Dónde personalizar

| Qué | Dónde |
| --- | --- |
| Reglas del equipo (lead, worker, coordinador) | `plugin/rules/*.md` o `budgit rules edit <tema>` |
| Agentes auxiliares | `plugin/agents/*.md` |
| Skills (kickoff, legal) | `plugin/skills/` |
| Guards (scripts que bloquean acciones) | `plugin/scripts/*guard*.py` |
| Hooks | `plugin/hooks/hooks.json` |
| Ajustes sin tocar código | `~/.budgit/config.json`, `budgit setup --force ...`, `budgit rules off <tema>` |

## Tests

El runner necesita Python 3.12 (como el CI). Lo ejecuto en Docker, sobre una copia, para no instalar nada en el Mac
ni tocar el plugin en vivo:

```bash
docker run --rm -v "$PWD":/src:ro python:3.12 sh -c 'cp -r /src /work && cd /work && git config --global --add safe.directory /work && git config --global user.email t@example.invalid && git config --global user.name t && python3 tools/runtests.py --workers 8 $(python3 tools/ci_shard.py 1/1)'
```

Para un solo módulo, cambia el final por `python3 tools/runtests.py test_<nombre>`.

**Cuidado:** la suite completa (~400 módulos, ~2000 s de CPU) consume mucha RAM con `--workers 8`. Usa `--workers 2`
o ejecuta solo los módulos de lo que hayas cambiado. Además, en el repo público la suite completa no pasa: 62 módulos ni
siquiera cargan porque esperan la estructura privada del autor (por ejemplo, un `skills/` en la raíz en vez de
`plugin/skills/`). El CI de upstream también está en rojo (comprobado el 2026-11-26).

## Brote

`budgit brote on | off | status | uninstall`. La app se copia a `~/Library/Application Support/BroteWindow/`.
Está firmada solo ad hoc (sin Developer ID); el código fuente está en `plugin/float/src/brote.swift`.
`BUDGIT_FLOAT_AUTO=off` evita que se abra sola.

## Mis cambios respecto a upstream

- `tools/runtests.py`: añade `plugin/scripts` a `sys.path` antes de `import safepath`
  (sin eso el runner fallaba con `ModuleNotFoundError`, también en el CI de upstream el 2026-11-26).
