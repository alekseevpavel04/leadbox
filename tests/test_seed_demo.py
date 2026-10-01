import sys
from pathlib import Path

from leadbox.models import FormStep, Source
from leadbox.services.leads import list_leads

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from seed_demo import LEADS, seed


async def test_seed_creates_demo_leads_once(session):
    assert await seed(session) == len(LEADS)
    assert await seed(session) == 0

    leads = await list_leads(session, tag="демо")
    assert len(leads) == len(LEADS)
    assert {lead.source for lead in leads} == set(Source)
    assert any(lead.form_step == FormStep.CONTACT for lead in leads)
    vk = next(lead for lead in leads if lead.campaign == "vk_autumn")
    assert {tag.name for tag in vk.tags} == {"бот", "демо", "горячий", "кампания:vk_autumn"}
