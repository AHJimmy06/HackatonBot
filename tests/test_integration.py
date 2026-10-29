import asyncio
import pytest
from app.persistence import database as db
from app.orchestration import conversations as service

@pytest.mark.asyncio
async def test_handover_discards_stale_answer(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'test.db'); db.init_db()
    begun=asyncio.Event(); release=asyncio.Event()
    async def slow(*args):
        begun.set(); await release.wait()
        return {'reply':'OBSOLETA','mood':'interesado','profile':{}},[]
    monkeypatch.setattr(service,'answer',slow)
    t=asyncio.create_task(service.handle_incoming(telegram_chat_id=-555,name='Prueba',username='',text='Python',origin='panel',deliver=False))
    await begun.wait(); cid=db.list_conversations()[0]['id']
    await service.operator_message(cid,'Atiendo yo')
    release.set(); await t
    assert db.get_conversation(cid)['mode']=='human'
    assert not any(m['content']=='OBSOLETA' for m in db.list_messages(cid))
