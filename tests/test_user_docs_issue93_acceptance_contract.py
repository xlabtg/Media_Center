from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def assert_markers(path: str, markers: list[str]) -> None:
    content = read_text(path)
    missing = [marker for marker in markers if marker not in content]

    assert not missing


def test_issue93_participant_user_guide_covers_core_self_service_scenarios() -> None:
    assert_markers(
        "docs/USER_GUIDE.md",
        [
            "# Руководство участника НМЦ",
            "Статус: pilot-ready для issue #93",
            "`nmc-pilot`",
            "## 1. Быстрый старт участника",
            "## 2. Онбординг 12-36 часов",
            "## 3. Ежедневные сценарии",
            "## 4. Вклад, баллы и МСЦ",
            "## 5. Согласия, ПДн и безопасность",
            "## 6. Поддержка и эскалации",
            "profile",
            "consents",
            "channels",
            "mentor_intro",
            "council_review",
            "docs/PILOT_TENANT_ONBOARDING.md",
            "docs/COMPLIANCE.md",
        ],
    )


def test_issue93_council_guide_is_separate_and_covers_hitl_governance() -> None:
    assert_markers(
        "docs/COUNCIL_GUIDE.md",
        [
            "# Инструкция Совета НМЦ",
            "Статус: pilot-ready для issue #93",
            "`council`",
            "`presidium`",
            "`board`",
            "## 1. Роли и ответственность",
            "## 2. Ежедневный цикл Совета",
            "## 3. HITL, вето и 2FA",
            "## 4. Пороговые решения и политики",
            "## 5. KPI, отчёты и ретроспектива",
            "## 6. Безопасность и compliance gate",
            "2/3",
            "8 часов",
            "ручной go/no-go",
            "hash-only",
            "docs/GOVERNANCE.md",
            "docs/STAGE_7_ACCEPTANCE.md",
        ],
    )


def test_issue93_faq_answers_participant_council_and_security_questions() -> None:
    faq = read_text("docs/FAQ.md")

    required_markers = [
        "# FAQ пилота НМЦ",
        "Статус: pilot-ready для issue #93",
        "## 1. Участникам",
        "## 2. Совету и Правлению",
        "## 3. Безопасность, ПДн и правила",
        "## 4. Поддержка пилота",
        "Что делать, если я не прошёл онбординг за 36 часов?",
        "Можно ли использовать реальные персональные данные в тестовых файлах?",
        "Когда возможны реальные выплаты?",
        "Как Совет накладывает вето?",
        "Как удалить или ограничить обработку моих данных?",
        "Куда обращаться при инциденте безопасности?",
    ]
    missing = [marker for marker in required_markers if marker not in faq]

    assert not missing
    assert faq.count("### ") >= 12


def test_issue93_docs_are_published_from_navigation_and_stage7_snapshot() -> None:
    readme = read_text("README.md")
    stage7 = read_text("docs/STAGE_7_ACCEPTANCE.md")
    pilot = read_text("docs/PILOT_TENANT_ONBOARDING.md")

    for marker in (
        "docs/USER_GUIDE.md",
        "docs/COUNCIL_GUIDE.md",
        "docs/FAQ.md",
    ):
        assert marker in readme
        assert marker in stage7
        assert marker in pilot

    for marker in (
        "Критерии приемки issue #93",
        "Документация опубликована и доступна",
        "Покрыты ключевые сценарии",
        "Совет имеет отдельные инструкции",
        "tests/test_user_docs_issue93_acceptance_contract.py",
    ):
        assert marker in stage7
