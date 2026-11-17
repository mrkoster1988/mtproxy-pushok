"""Синтетические ответы протокола, не запись живого диалога @MTProxyBot."""
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from mtproxy_bot import MTProxyDialog, BotProtocolError, card_tag, promoted_channel, endpoints, username

TAG='a'*32
HOST='proxy.example.org'


def card(target='sample_channel', tag=TAG, host=HOST):
    return NS(id=100,raw_text=f'Address: {host}:8443\nSecret: '+ 'b'*32 + f'\nTag: {tag}\nPromoted channel: @{target}',entities=[])


class BotParserTests(unittest.TestCase):
    def test_tag_is_not_connection_secret_and_node_must_match(self):
        self.assertEqual(card_tag(card(),[HOST],8443), TAG)
        with self.assertRaisesRegex(BotProtocolError,'proxy_identity'):
            card_tag(card(),['another.example.org'],8443)
        for value in ['Secret: '+TAG, 'Tag: '+'0'*32, 'Tag: '+TAG+'\nTag: '+'c'*32]:
            with self.assertRaises(BotProtocolError):
                card_tag(NS(raw_text=HOST+':8443\n'+value,entities=[]),[HOST],8443)

    def test_endpoint_separate_fields_and_proxy_url(self):
        self.assertIn((HOST,8443),endpoints(f'Address: {HOST}\nPort: 8443'))
        self.assertIn((HOST,8443),endpoints(f'tg://proxy?server={HOST}&port=8443&secret=secret'))
        self.assertNotIn((HOST,8443),endpoints('evil'+HOST+':8443'))

    def test_channel_field_not_random_mention_and_utf16_link(self):
        self.assertEqual(promoted_channel(card('Test_Channel')),'test_channel')
        self.assertIsNone(promoted_channel(NS(raw_text='Send @test_channel',entities=[])))
        content='🐱 Proxy\nPromotion: Имя канала'
        offset=len(content.split('Имя')[0].encode('utf-16-le'))//2
        self.assertEqual(promoted_channel(NS(raw_text=content,entities=[NS(offset=offset,url='https://t.me/test_channel')])),'test_channel')
        for bad in ['https://evil.test/sample','https://t.me/+private','@name\n/delete']:
            with self.assertRaises(BotProtocolError):username(bad)


class BotFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client=NS(get_entity=AsyncMock(return_value=NS(id=321,broadcast=True,username='sample_channel')))
        self.dialog=MTProxyDialog(self.client,NS(id=123))
        self.request=dict(action='bind',hosts=[HOST],port=8443,expected_tag=TAG,channel_url='https://t.me/sample_channel')

    async def test_retry_already_changed_only_reads_and_verifies_fresh_card(self):
        self.dialog.select=AsyncMock(side_effect=[card(),card()])
        self.dialog.click=AsyncMock();self.dialog.send=AsyncMock()
        result=await self.dialog.prepare(self.request)
        self.assertEqual(result['tag'],TAG);self.assertEqual(result['telegram_channel_id'],321)
        self.assertEqual(self.dialog.select.await_count,2)
        self.dialog.click.assert_not_called();self.dialog.send.assert_not_called()

    async def test_success_reply_alone_is_not_verification(self):
        old=card('old_channel')
        old.buttons=[[NS(text='Set promotion',button=type('KeyboardButtonCallback',(),{})())]]
        self.dialog.select=AsyncMock(side_effect=[old,card('wrong_channel')])
        self.dialog.click=AsyncMock(return_value=NS(raw_text='Send a public channel link'))
        self.dialog.send=AsyncMock(return_value=NS(raw_text='Success'))
        with self.assertRaisesRegex(BotProtocolError,'binding_not_verified'):
            await self.dialog.prepare(self.request)
        self.dialog.send.assert_awaited_once_with('https://t.me/sample_channel')

    async def test_changed_tag_or_channel_identity_stops_before_write(self):
        self.dialog.select=AsyncMock(return_value=card(tag='d'*32))
        self.dialog.click=AsyncMock()
        with self.assertRaisesRegex(BotProtocolError,'tag_changed'):
            await self.dialog.prepare(self.request)
        self.dialog.click.assert_not_called()
        with self.assertRaisesRegex(BotProtocolError,'target_changed'):
            await self.dialog.prepare(dict(self.request,telegram_channel_id=999))

    async def test_unsupported_button_or_prompt_never_sends_channel(self):
        for button_type,prompt in [('KeyboardButtonUrl','Send a channel'),('KeyboardButtonCallback','Send your phone number')]:
            old=card('old_channel');old.buttons=[[NS(text='Set promotion',button=type(button_type,(),{})())]]
            self.dialog.select=AsyncMock(return_value=old)
            self.dialog.click=AsyncMock(return_value=NS(raw_text=prompt));self.dialog.send=AsyncMock()
            with self.assertRaises(BotProtocolError):await self.dialog.prepare(self.request)
            self.dialog.send.assert_not_called()

    async def test_fresh_accepts_edited_bot_message_but_not_outgoing_echo(self):
        bot=NS(id=123);previous=card();previous.buttons=[]
        edited=card('other_channel');edited.buttons=[];edited.out=False;edited.sender_id=123
        client=NS(get_messages=AsyncMock(side_effect=[[NS(out=True,sender_id=444)],edited]))
        from mtproxy_bot import snapshot
        result=await MTProxyDialog(client,bot).fresh(100,100,snapshot(previous))
        self.assertEqual(result,edited)

    async def test_ambiguous_proxy_buttons_stop_without_click(self):
        button=lambda: NS(text=HOST+':8443',button=type('KeyboardButtonCallback',(),{})())
        msg=NS(raw_text='Your proxies',buttons=[[button(),button()]])
        self.dialog.send=AsyncMock(return_value=msg);self.dialog.click=AsyncMock()
        with self.assertRaisesRegex(BotProtocolError,'proxy_ambiguous'):
            await self.dialog.select([HOST],8443)
        self.dialog.click.assert_not_called()
