"""Fill the CRM with a few demo leads tagged «демо», so a reviewer does not open an empty list.

Run once against the database from DATABASE_URL: `PYTHONPATH=src python scripts/seed_demo.py`.
A second run does nothing while any lead tagged «демо» exists. Timestamps are the real moment of
seeding: the leads are marked as demo instead of pretending to have come in earlier.
"""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from leadbox.config import get_settings
from leadbox.db import create_engine, create_sessionmaker
from leadbox.models import FormStep, Source, Status
from leadbox.services.leads import create_lead, list_leads, set_status
from leadbox.services.tags import add_tag

DEMO_TAG = "демо"

LEADS = [
    {
        "source": Source.BOT,
        "name": "Марина",
        "contact": "+79161234567",
        "request": "Нужна реклама для студии растяжки во ВКонтакте, бюджет около 40 тысяч в месяц",
        "campaign": "vk_autumn",
        "form_step": FormStep.DONE,
        "tags": ["горячий"],
    },
    {
        "source": Source.BOT,
        "name": "Игорь Петров",
        "contact": "@igor_autoservice",
        "request": "Автосервис. Платим за клики в Директе, а заявок почти нет. Хотим разобраться, что не так",
        "campaign": "yandex_search",
        "form_step": FormStep.DONE,
        "status": Status.IN_PROGRESS,
    },
    {
        "source": Source.BOT,
        "name": "Алина",
        "campaign": "tg_ads_oct",
        "form_step": FormStep.CONTACT,
    },
    {
        "source": Source.TELEGRAM,
        "name": "Сергей Волков",
        "contact": "@svolkov",
        "request": "Добрый день! Сколько стоит настройка таргета для интернет-магазина одежды?",
    },
    {
        "source": Source.TELEGRAM,
        "name": "Ольга",
        "contact": "@olga_cakes",
        "request": "Кондитерская, хотим запустить рекламу к Новому году",
        "status": Status.WON,
        "tags": ["повторный клиент"],
    },
    {
        "source": Source.MANUAL,
        "name": "Анна Кузнецова",
        "contact": "anna.k@example.com",
        "request": "Познакомились на конференции, интересует аудит рекламного кабинета",
    },
    {
        "source": Source.MANUAL,
        "name": "ООО «СтройДом»",
        "contact": "+74951234567",
        "request": "Входящий звонок: нужно SEO, такую услугу не оказываем",
        "status": Status.LOST,
    },
]


async def seed(session: AsyncSession) -> int:
    """Create the demo leads unless they are already there; returns how many were created."""
    if await list_leads(session, tag=DEMO_TAG):
        return 0
    for spec in LEADS:
        spec = dict(spec)
        tags = spec.pop("tags", [])
        status = spec.pop("status", None)
        campaign = spec.get("campaign")
        lead = await create_lead(session, **spec)
        for tag in [DEMO_TAG, *tags, *([f"кампания:{campaign}"] if campaign else [])]:
            await add_tag(session, lead, tag)
        if status is not None:
            await set_status(session, lead, status)
    await session.commit()
    return len(LEADS)


async def main() -> None:
    engine = create_engine(get_settings().database_url)
    try:
        async with create_sessionmaker(engine)() as session:
            created = await seed(session)
    finally:
        await engine.dispose()
    print(f"demo leads created: {created}" if created else "demo leads already present, nothing to do")


if __name__ == "__main__":
    asyncio.run(main())
