# ADR-0009: Имена сервисных образов в GHCR

- **Статус:** Accepted
- **Дата:** 2026-06-23
- **Связанный issue:** [#235](https://github.com/xlabtg/Media_Center/issues/235)

## Контекст

Эпик C Этапа 9 публикует сервисные Docker-образы в GitHub Container Registry.
Исходный материал case-study предлагал короткий префикс `nmc-`, например
`ghcr.io/${owner}/nmc-${service}`. В текущем CI и операционных документах уже
используется схема `media-center-`, например
`ghcr.io/${owner}/media-center-${service}`.

Для C2 нужно зафиксировать единый registry namespace и не разорвать будущие
deploy/runbook-ссылки на сервисные образы.

## Решение

Сохраняем имя сервисного образа:

```text
ghcr.io/${owner}/media-center-${service}
```

Префикс `media-center-` становится baseline для всех сервисных образов в GHCR.
Префикс `nmc-` не используется для новых публикаций, чтобы не поддерживать два
набора тегов и не плодить неоднозначность между историческим названием НМЦ и
техническим именем репозитория `Media_Center`.

Workflow публикации должен выпускать для каждого сервиса:

- `${version}` для релизного тега;
- `${major}.${minor}` для релизного тега;
- `${sha}` для точной привязки к commit;
- `latest` только на tag push, чтобы тег указывал на последний релиз, а не на
  произвольный commit в `main`.

Логин в GHCR выполняется через `docker/login-action` с
`secrets.GITHUB_TOKEN`; отдельные registry-секреты для штатной публикации не
нужны.

## Последствия

- Потребители образов используют только `media-center-<service>`.
- Push в `main` продолжает собирать и публиковать sha-тег для проверки
  переносимости образа.
- Push semver-подобного git-тега публикует version, major.minor, sha и latest.
- Если понадобится миграция к `nmc-`, она должна идти отдельным ADR с планом
  двойной публикации, deprecation window и обновлением downstream-ссылок.

## Связанные документы

- [issue-213/02-gap-analysis.md](../case-studies/issue-213/02-gap-analysis.md)
- [issue-213/03-research-and-libraries.md](../case-studies/issue-213/03-research-and-libraries.md)
- [issue-213/05-solution-plan.md](../case-studies/issue-213/05-solution-plan.md)
