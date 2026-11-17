"""FloodWait сохраняется на диск и распространяется на все задания аккаунта."""
import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import telegram_owner as owner
import telegram_binding_worker as worker
import automatic_binding as binding


class FloodWaitError(Exception):
    def __init__(self,seconds):self.seconds=seconds


class CooldownTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'owner'
        clock=patch.object(owner.time,'time',return_value=1000.2)
        self.clock=clock.start();self.addCleanup(clock.stop)
        self.client=NS(connect=AsyncMock(),disconnect=AsyncMock(),get_me=AsyncMock(return_value=NS(id=42,bot=False,username='owner')),
                       get_entity=AsyncMock(return_value=NS(bot=True,username='MTProxyBot')))

    def test_worker_flood_wait_persists_before_unlock_and_blocks_next_job_without_rpc(self):
        dialog=NS(register=AsyncMock(side_effect=FloodWaitError(900000)),prepare=AsyncMock())
        with owner.PrivateState(self.path) as state,patch.object(worker,'MTProxyDialog',return_value=dialog):
            with self.assertRaises(owner.TelegramCooldown) as caught:
                asyncio.run(worker.run(self.client,{'user_id':42},{'action':'register'},state))
            self.assertGreaterEqual(caught.exception.retry_seconds,900000)
        self.assertEqual((self.path/'telegram-cooldown.json').stat().st_mode&0o777,0o600)
        self.client.connect.reset_mock();self.client.get_entity.reset_mock()
        self.clock.return_value=1300.2
        with owner.PrivateState(self.path) as state:
            with self.assertRaises(owner.TelegramCooldown) as caught:
                asyncio.run(worker.run(self.client,{'user_id':42},{'action':'bind'},state))
            self.assertGreaterEqual(caught.exception.retry_seconds,899700)
        self.client.connect.assert_not_called();self.client.get_entity.assert_not_called();dialog.prepare.assert_not_called()

    def test_cooldown_does_not_shorten_and_expires_after_restart(self):
        with owner.PrivateState(self.path) as state:
            state.defer_telegram(42,3600)
            before=state.read_file('telegram-cooldown.json')['not_before']
        self.clock.return_value=1100.2
        with owner.PrivateState(self.path) as state:
            state.defer_telegram(42,1)
            self.assertGreaterEqual(state.read_file('telegram-cooldown.json')['not_before'],before)
            self.assertEqual(state.cooldown(43),0)
        self.clock.return_value=4700
        with owner.PrivateState(self.path) as state:state.require_available(42)

    def test_manual_owner_check_respects_pause_but_disconnect_is_available(self):
        with owner.PrivateState(self.path) as state:
            saved={'user_id':42,'session':'private-session','api_id':123,'api_hash':'a'*32};state.save(saved)
            state.defer_telegram(42,3600)
            with self.assertRaises(owner.TelegramCooldown):
                asyncio.run(owner.operate(self.client,'status',state,{},saved,NS()))
            self.client.connect.assert_not_called()
            self.client.log_out=AsyncMock(return_value=True)
            result=asyncio.run(owner.operate(self.client,'disconnect',state,{},saved,NS()))
            self.assertEqual(result['state'],'disconnected');self.client.log_out.assert_called_once()

    def test_binding_does_not_clear_current_sponsor_or_claim_job_during_pause(self):
        agent=NS(exchange=Mock(),apply=Mock())
        with patch.object(binding,'enabled',return_value=True),patch.object(binding,'connection_status',return_value={
            'enabled':True,'helper_available':True,'state':'connected','retry_seconds':3600}):
            binding.tick(agent,{'host':'8.8.8.8','port':8443})
        agent.exchange.assert_not_called();agent.apply.assert_not_called()

    def test_corrupt_cooldown_fails_closed(self):
        with owner.PrivateState(self.path) as state:
            state.save_file('telegram-cooldown.json',{'user_id':42,'not_before':'bad'})
            with self.assertRaisesRegex(owner.OwnerError,'invalid_state'):state.require_available(42)
