"""Обезличенные форматы карточек и синтетические сценарии @MTProxyBot."""
import asyncio
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from mtproxy_bot import MTProxyDialog, BotProtocolError, card_tag, promoted_channel, endpoints, username, resolve_channel, list_proxy, buttons, snapshot

try:
    from telethon import Button
    from telethon.tl import types
    from telethon.tl.custom.messagebutton import MessageButton
except ImportError:
    Button = None

TAG='a'*32
HOST='proxy.example.org'


@unittest.skipUnless(Button is not None, 'Нужна закреплённая версия Telethon')
class SDKButtonTests(unittest.IsolatedAsyncioTestCase):
    def wrap(self, original):
        return MessageButton(None, getattr(original, 'button', original), None, None, 100)

    async def test_installed_sdk_callback_finds_proxy_and_preserves_payload(self):
        button = self.wrap(Button.inline(HOST+' '+TAG, b'proxy-fixture'))
        listing = NS(raw_text='Here is the list of all proxies you created:', buttons=[[button]])
        dialog = MTProxyDialog(None, None)
        dialog.send = AsyncMock(return_value=listing)
        dialog.click = AsyncMock(return_value=card())
        self.assertEqual(await dialog.select([HOST], 8443), dialog.click.return_value)
        dialog.click.assert_awaited_once_with(listing, button)
        self.assertEqual(snapshot(listing)[1], ((HOST+' '+TAG, b'proxy-fixture'),))

    def test_only_callback_and_plain_text_are_allowed(self):
        allowed = [self.wrap(Button.inline('Set promotion', b'fixture')),
                   self.wrap(Button.text('Next'))]
        blocked = [self.wrap(Button.url('Set promotion', 'https://example.org')),
                   self.wrap(Button.request_phone('Next')),
                   self.wrap(Button.request_location('Next')),
                   self.wrap(Button.switch_inline('Next', 'fixture')),
                   self.wrap(Button.buy('Next')),
                   self.wrap(types.KeyboardInlineButton('Set promotion', types.InlineButtonTypeCallback(b'fixture', requires_password=True)))]
        self.assertEqual(list(buttons(NS(buttons=[allowed+blocked]))), allowed)


def card(target='sample_channel', tag=TAG, host=HOST):
    return NS(id=100,raw_text=f'Address: {host}:8443\nSecret: '+ 'b'*32 + f'\nTag: {tag}\nPromoted channel: @{target}',entities=[])


def prose_card(target='sample_channel', tag=TAG, *, russian=False):
    # Английский оригинал со скриншота; адрес, тег и ссылка обезличены.
    prefix = (f'Прокси-сервер: {HOST}:8443 с тегом {tag}.\nРекомендованный канал: '
              if russian else f'Proxy {HOST}:8443 with the tag {tag}.\nPromoted channel: ')
    title = ':// Канал 🔐 Новости'
    content = prefix + title + '.\nHow can I help?'
    entity = NS(offset=len(prefix.encode('utf-16-le'))//2,
                length=len(title.encode('utf-16-le'))//2, url='https://t.me/'+target)
    return NS(id=100, raw_text=content, entities=[entity])


class BotParserTests(unittest.TestCase):
    def test_tag_in_card_sentence_and_translated_promotion_link(self):
        for russian in (False, True):
            value = prose_card(russian=russian)
            self.assertEqual(card_tag(value, [HOST], 8443), TAG)
            self.assertEqual(promoted_channel(value), 'sample_channel')
        value = prose_card(tag=TAG.upper())
        value.raw_text = value.raw_text.replace('with the tag ', 'with the tag\n')
        self.assertEqual(card_tag(value, [HOST], 8443), TAG)

    def test_original_and_legacy_card_headers(self):
        for header in ('Proxy ', 'Proxy: ', 'Proxy server: ', 'Proxy server ', 'PROXY '):
            for label in ('with tag ', 'with the tag '):
                with self.subTest(header=header, label=label):
                    value = prose_card()
                    value.raw_text = value.raw_text.replace('Proxy ', header, 1).replace('with the tag ', label)
                    self.assertEqual(card_tag(value, [HOST], 8443), TAG)

    def test_original_card_rejects_truncated_or_unrelated_tag(self):
        for value in (
                prose_card(tag='...12345678'),
                prose_card(tag=TAG[:-1]),
                NS(raw_text=f'This allows you to set up a promoted channel for your proxy {HOST}:8443 with the tag {TAG}.', entities=[]),
                NS(raw_text=f'{HOST}:8443\nPromoted channel: Proxy {HOST}:8443 with the tag {TAG}.', entities=[]),
                NS(raw_text=f'Proxy{HOST}:8443 with the tag {TAG}.', entities=[])):
            with self.subTest(text=value.raw_text), self.assertRaises(BotProtocolError):
                card_tag(value, [HOST], 8443)

    def test_prose_tag_rejects_wrong_identity_secret_and_ambiguous_values(self):
        for value in (prose_card(tag='0'*32), prose_card(tag=TAG+'c'),
                      NS(raw_text=f'{HOST}:8443\nSecret: {TAG}', entities=[]),
                      NS(raw_text=f'{HOST}:8443\nPromoted channel: with tag {TAG}', entities=[])):
            with self.assertRaises(BotProtocolError):
                card_tag(value, [HOST], 8443)
        value = prose_card()
        with self.assertRaisesRegex(BotProtocolError, 'proxy_identity_not_verified'):
            card_tag(value, [HOST], 9443)
        value.raw_text += '\nTag: ' + 'c'*32
        with self.assertRaisesRegex(BotProtocolError, 'tag_not_verified'):
            card_tag(value, [HOST], 8443)
        value = prose_card()
        value.raw_text = value.raw_text.replace(HOST, 'other.example.org') + f'\n{HOST}:8443'
        with self.assertRaisesRegex(BotProtocolError, 'proxy_identity_not_verified'):
            card_tag(value, [HOST], 8443)

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

    def test_proxy_list_address_and_tag_without_port(self):
        self.assertEqual(list_proxy('192.0.2.10 ' + TAG), ('192.0.2.10', TAG))
        self.assertEqual(list_proxy('  PROXY.example.org ' + TAG.upper()), (HOST, TAG))
        for label in (HOST, HOST+':8443', HOST+' '+TAG+' extra', HOST+' '+'0'*32,
                      'https://'+HOST+' '+TAG, HOST+' '+TAG[:-1]):
            self.assertIsNone(list_proxy(label))

    def test_channel_field_not_random_mention_and_utf16_link(self):
        self.assertEqual(promoted_channel(card('Test_Channel')),'test_channel')
        self.assertIsNone(promoted_channel(NS(raw_text='Send @test_channel',entities=[])))
        content='🐱 Proxy\nPromotion: Имя канала'
        offset=len(content.split('Имя')[0].encode('utf-16-le'))//2
        self.assertEqual(promoted_channel(NS(raw_text=content,entities=[NS(offset=offset,url='https://t.me/test_channel')])),'test_channel')
        for bad in ['https://evil.test/sample','https://t.me/+private','@name\n/delete']:
            with self.assertRaises(BotProtocolError):username(bad)

    def test_channel_links_match_supported_cabinet_hosts(self):
        for link in ('https://telegram.me/Sample_Channel/', 'https://T.ME/Sample_Channel', '@Sample_Channel'):
            self.assertEqual(username(link), 'sample_channel')
        for link in ('https://t.me/sample_channel/123', 'https://t.me/sample_channel?start=x',
                     'https://t.me.evil/sample_channel', 'https://user@t.me/sample_channel'):
            with self.assertRaises(BotProtocolError):username(link)


class BotFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client=NS(get_entity=AsyncMock(return_value=NS(id=321,broadcast=True,username='sample_channel')))
        self.dialog=MTProxyDialog(self.client,NS(id=123))
        self.request=dict(action='bind',hosts=[HOST],port=8443,expected_tag=TAG,channel_url='https://t.me/sample_channel')

    async def test_myproxies_opens_card_directly_inspect_then_bind_and_read_back(self):
        empty = NS(id=11, raw_text=f'Proxy {HOST}:8443 with the tag {TAG}.\nPromoted channel: n/a.\nHow can I help?', entities=[])
        set_promotion = NS(text='Set promotion', button=type('KeyboardButtonCallback', (), {})())
        empty.buttons = [[set_promotion]]
        final = prose_card()
        self.dialog.send = AsyncMock(side_effect=[empty, empty, NS(raw_text='Success'), final])
        self.dialog.click = AsyncMock(return_value=NS(raw_text='Send a public channel link'))
        inspected = await self.dialog.prepare(dict(self.request, action='inspect'))
        self.assertEqual(inspected['tag'], TAG)
        self.assertTrue(inspected['promotion_empty'])
        self.assertIsNone(inspected['channel_username'])
        self.dialog.click.assert_not_awaited()
        result = await self.dialog.prepare(self.request)
        self.assertEqual(result['channel_username'], 'sample_channel')
        self.dialog.click.assert_awaited_once_with(empty, set_promotion)
        self.assertEqual([call.args[0] for call in self.dialog.send.await_args_list],
                         ['/myproxies', '/myproxies', 'https://t.me/sample_channel', '/myproxies'])

    async def test_direct_card_with_invalid_or_ambiguous_identity_stops_before_click(self):
        for header in (f'Proxy {HOST}:9443 with the tag {TAG}.',
                       f'Proxy {HOST}:8443 with the tag {"0"*32}.',
                       f'Proxy {HOST}:8443 with the tag {TAG}.\nProxy {HOST}:9443 with the tag {TAG}.'):
            self.dialog.send = AsyncMock(return_value=NS(raw_text=header+'\nPromoted channel: n/a.', entities=[]))
            self.dialog.click = AsyncMock()
            with self.subTest(header=header), self.assertRaises(BotProtocolError):
                await self.dialog.prepare(self.request)
            self.dialog.click.assert_not_awaited()

    async def test_other_direct_card_requires_verified_selection_from_list(self):
        first = prose_card()
        first.raw_text = first.raw_text.replace(':8443', ':9443')
        back = NS(text='« Back to list', button=type('KeyboardButtonCallback', (), {})())
        select = NS(text=HOST+':8443', button=type('KeyboardButtonCallback', (), {})())
        first.buttons = [[back]]
        listing = NS(raw_text='Proxies', buttons=[[select]], entities=[])
        current = prose_card()
        self.dialog.send = AsyncMock(return_value=first)
        self.dialog.click = AsyncMock(side_effect=[listing, current])
        self.assertIs(await self.dialog.select([HOST], 8443), current)
        self.assertEqual([c.args for c in self.dialog.click.await_args_list], [(first, back), (listing, select)])

    async def test_prose_card_with_edit_promotion_verifies_fresh_channel(self):
        old = prose_card('old_channel')
        edit = NS(text='Edit promotion', button=type('KeyboardButtonCallback',(),{})())
        remove = NS(text='Remove promotion', button=type('KeyboardButtonCallback',(),{})())
        old.buttons = [[edit, remove]]
        self.dialog.select = AsyncMock(side_effect=[old, prose_card()])
        self.dialog.click = AsyncMock(return_value=NS(raw_text='Send a public channel link'))
        self.dialog.send = AsyncMock(return_value=NS(raw_text='Success'))
        result = await self.dialog.prepare(self.request)
        self.assertEqual(result['tag'], TAG)
        self.assertEqual(result['channel_username'], 'sample_channel')
        self.dialog.click.assert_awaited_once_with(old, edit)
        self.dialog.send.assert_awaited_once_with('https://t.me/sample_channel')

    async def test_prose_card_already_promoted_does_not_change_channel(self):
        self.dialog.select = AsyncMock(side_effect=[prose_card(), prose_card()])
        self.dialog.click = AsyncMock()
        self.dialog.send = AsyncMock()
        result = await self.dialog.prepare(self.request)
        self.assertEqual(result['channel_username'], 'sample_channel')
        self.dialog.click.assert_not_called()
        self.dialog.send.assert_not_called()

    async def test_multiple_same_ip_proxies_are_selected_by_verified_port(self):
        listing=NS(raw_text='Proxies',buttons=[[NS(text=HOST+' '+tag,button=type('KeyboardButtonCallback',(),{})()) for tag in (TAG,'c'*32)]])
        other=card(tag='c'*32);other.raw_text=other.raw_text.replace('8443','9443')
        self.dialog.send=AsyncMock(return_value=listing)
        self.dialog.click=AsyncMock(side_effect=[card(),other,card()])
        result=await self.dialog.select([HOST],8443)
        self.assertEqual(card_tag(result,[HOST],8443),TAG)
        self.assertEqual(self.dialog.click.await_count,3)
        self.dialog.click=AsyncMock(side_effect=[card(),other])
        with self.assertRaisesRegex(BotProtocolError,'proxy_not_found'):
            await self.dialog.select([HOST],10443)
        self.dialog.click=AsyncMock(side_effect=[card(),card(tag='c'*32)])
        with self.assertRaisesRegex(BotProtocolError,'proxy_ambiguous'):
            await self.dialog.select([HOST],8443)

    async def test_registration_uses_existing_instance_without_newproxy(self):
        from unittest.mock import Mock
        from uuid import uuid4
        request=dict(action='register',host='8.8.8.8',port=8443,secret='b'*32,node_id=str(uuid4()))
        state=Mock(read_file=Mock(return_value=None))
        self.dialog.select=AsyncMock(return_value=card(host='8.8.8.8'))
        self.dialog.send=AsyncMock()
        self.assertEqual((await self.dialog.register(request,state))['tag'],TAG)
        self.dialog.send.assert_not_called();state.save_file.assert_called_once()

    async def test_registration_records_intent_before_secret_and_verifies_card(self):
        from unittest.mock import Mock
        from uuid import uuid4
        request=dict(action='register',host='8.8.8.8',port=8443,secret='b'*32,node_id=str(uuid4()))
        state=Mock(read_file=Mock(return_value=None))
        self.dialog.select=AsyncMock(side_effect=[BotProtocolError('proxy_not_found'),card(host='8.8.8.8')])
        async def send(value):
            if value=='/newproxy':return NS(raw_text='Send IP address and port')
            if value=='8.8.8.8:8443':return NS(raw_text='Send the proxy secret')
            self.assertEqual(value,'b'*32);state.save_file.assert_called_once()
            return NS(raw_text='Registered')
        self.dialog.send=AsyncMock(side_effect=send)
        self.assertEqual((await self.dialog.register(request,state))['tag'],TAG)
        self.assertEqual(self.dialog.select.await_count,2)

    async def test_uncertain_registration_never_repeats_newproxy(self):
        from unittest.mock import Mock
        from uuid import uuid4
        request=dict(action='register',host='8.8.8.8',port=8443,secret='b'*32,node_id=str(uuid4()))
        state=Mock(read_file=Mock(return_value={'host':'8.8.8.8','port':8443}))
        self.dialog.select=AsyncMock(side_effect=BotProtocolError('proxy_not_found'))
        self.dialog.send=AsyncMock()
        with self.assertRaisesRegex(BotProtocolError,'registration_uncertain'):
            await self.dialog.register(request,state)
        self.dialog.send.assert_not_called()

    async def test_registration_accepts_actual_hex_prompt_and_sends_existing_secret_once(self):
        from unittest.mock import Mock, call
        from uuid import uuid4
        request=dict(action='register',host='8.8.8.8',port=8443,secret='b'*32,node_id=str(uuid4()))
        marker='registration-'+request['node_id']+'.json'
        identity={'host':request['host'],'port':request['port']}
        state=Mock(read_file=Mock(return_value=None))
        self.dialog.select=AsyncMock(side_effect=[BotProtocolError('proxy_not_found'),card(host=request['host'])])
        async def send(value):
            if value=='/newproxy':
                return NS(raw_text='Registering a new proxy server. Please send me its address in the format host:port.')
            if value=='8.8.8.8:8443':
                return NS(raw_text='Now please specify its secret in hex format.')
            self.assertEqual(value,request['secret'])
            state.save_file.assert_called_once_with(marker,identity)
            return NS(raw_text='Registered')
        self.dialog.send=AsyncMock(side_effect=send)
        result=await self.dialog.register(request,state)
        self.assertEqual(result['tag'],TAG)
        self.assertEqual(self.dialog.send.await_args_list,[call('/newproxy'),call('8.8.8.8:8443'),call(request['secret'])])
        self.assertEqual(state.save_file.call_args_list,[call(marker,identity),call(marker,dict(identity,tag=TAG,secret_sha256=hashlib.sha256(request['secret'].encode()).hexdigest()))])

    async def test_bare_registration_card_recovers_only_from_matching_conversation(self):
        from unittest.mock import Mock
        from uuid import uuid4
        request = dict(host='8.8.8.8', port=9443, secret='b'*32, node_id=str(uuid4()))
        state = Mock(read_file=Mock(return_value=dict(host=request['host'], port=request['port'])))
        card = NS(raw_text=f"Proxy 8.8.8.8:9443 with the tag {TAG}.\nPromoted channel: n/a.", id=85, entities=[])
        self.dialog.select = AsyncMock(return_value=card)
        self.dialog.send = AsyncMock()
        history = [NS(out=not i%2, sender_id=self.dialog.bot.id if i%2 else 42, raw_text=value)
                   for i, value in enumerate(['/newproxy','Send IP and port','8.8.8.8:9443',
                         'Specify its secret in hex format',request['secret'],f'Success! Your proxy tag: {TAG}'])]
        self.client.get_messages = AsyncMock(return_value=list(reversed(history)))
        self.assertEqual((await self.dialog.register(request,state))['tag'], TAG)
        history[4].raw_text = 'c'*32
        with self.assertRaisesRegex(BotProtocolError, 'registration_uncertain'):
            await self.dialog.register(request,state)
        self.dialog.send.assert_not_awaited()

    async def test_secret_is_not_sent_for_unrecognised_second_prompt(self):
        from unittest.mock import Mock, call
        from uuid import uuid4
        request=dict(action='register',host='8.8.8.8',port=8443,secret='b'*32,node_id=str(uuid4()))
        for prompt in ('Now please specify your login code.', 'Secret is invalid.', 'Please send your password.'):
            with self.subTest(prompt=prompt):
                state=Mock(read_file=Mock(return_value=None))
                self.dialog.select=AsyncMock(side_effect=BotProtocolError('proxy_not_found'))
                self.dialog.send=AsyncMock(side_effect=[NS(raw_text='Send IP address and port'),NS(raw_text=prompt)])
                with self.assertRaisesRegex(BotProtocolError,'registration_prompt_invalid'):
                    await self.dialog.register(request,state)
                self.assertEqual(self.dialog.send.await_args_list,[call('/newproxy'),call('8.8.8.8:8443')])
                state.save_file.assert_not_called()

    async def test_registration_wrong_secret_and_unrecognised_prompt_are_rejected(self):
        from unittest.mock import Mock
        from uuid import uuid4
        request=dict(action='register',host='8.8.8.8',port=8443,secret='d'*32,node_id=str(uuid4()))
        state=Mock(read_file=Mock(return_value=None))
        self.dialog.select=AsyncMock(return_value=card(host='8.8.8.8'))
        with self.assertRaisesRegex(BotProtocolError,'registration_secret_mismatch'):
            await self.dialog.register(request,state)
        state.save_file.assert_not_called()
        self.dialog.select=AsyncMock(side_effect=BotProtocolError('proxy_not_found'))
        self.dialog.send=AsyncMock(return_value=NS(raw_text='Send your login code'))
        with self.assertRaisesRegex(BotProtocolError,'registration_prompt_invalid'):
            await self.dialog.register(request,state)
        self.dialog.send.assert_awaited_once_with('/newproxy')

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

    async def test_proxy_list_host_tag_candidate_must_match_full_card(self):
        button = NS(text=HOST+' '+TAG, button=type('KeyboardButtonCallback',(),{})())
        listing = NS(raw_text='Here is a list of all the proxies you created:', buttons=[[button]])
        self.dialog.send=AsyncMock(return_value=listing)
        self.dialog.click=AsyncMock(return_value=card())
        self.assertEqual(await self.dialog.select([HOST],8443), self.dialog.click.return_value)
        self.dialog.click.assert_awaited_once_with(listing,button)
        for value,code in ((card(host='other.example.org'),'proxy_identity_not_verified'),
                           (card(tag='c'*32),'tag_changed'),
                           (NS(raw_text=HOST+':443\nTag: '+TAG,entities=[]),'proxy_identity_not_verified'),
                           (NS(raw_text='Address: '+HOST+'\nTag: '+TAG,entities=[]),'proxy_identity_not_verified')):
            self.dialog.click=AsyncMock(return_value=value)
            with self.assertRaisesRegex(BotProtocolError,'^'+code+'$'):
                await self.dialog.select([HOST],8443)

    async def test_host_tag_list_does_not_accept_similar_host_url_or_duplicates(self):
        for labels,kind,code in ((['evil'+HOST+' '+TAG],'KeyboardButtonCallback','proxy_not_found'),
                                 ([HOST+' '+TAG],'KeyboardButtonUrl','proxy_not_found'),
                                 ([HOST+' '+TAG,HOST+' '+TAG],'KeyboardButtonCallback','proxy_ambiguous')):
            buttons = [NS(text=value,button=type(kind,(),{})()) for value in labels]
            self.dialog.send=AsyncMock(return_value=NS(raw_text='Proxies',buttons=[buttons]))
            self.dialog.click=AsyncMock()
            with self.assertRaisesRegex(BotProtocolError,'^'+code+'$'):
                await self.dialog.select([HOST],8443)
            self.dialog.click.assert_not_called()

    async def test_bind_from_host_tag_listing_reopens_and_verifies_card(self):
        listing=NS(raw_text='Your proxies',buttons=[[NS(text=HOST+' '+TAG,
                   button=type('KeyboardButtonCallback',(),{})())]])
        old=card('old_channel');old.buttons=[[NS(text='Set promotion',button=type('KeyboardButtonCallback',(),{})())]]
        self.dialog.send=AsyncMock(side_effect=[listing,NS(raw_text='Success'),listing])
        self.dialog.click=AsyncMock(side_effect=[old,NS(raw_text='Send a public channel link'),card()])
        result=await self.dialog.prepare(self.request)
        self.assertEqual(result['tag'],TAG)
        self.assertEqual(result['channel_username'],'sample_channel')
        self.assertEqual([c.args[0] for c in self.dialog.send.await_args_list],
                         ['/myproxies','https://t.me/sample_channel','/myproxies'])

    async def test_active_additional_username_and_bot_primary_name_match_by_id(self):
        channel=NS(id=321,broadcast=True,username='primary_channel',
                   usernames=[NS(username='sample_channel',active=True)])
        self.client.get_entity.return_value=channel
        self.dialog.select=AsyncMock(return_value=card('primary_channel'))
        self.dialog.send=AsyncMock();self.dialog.click=AsyncMock()
        for action in ('inspect','bind'):
            result=await self.dialog.prepare(dict(self.request,action=action,channel_url='https://telegram.me/sample_channel'))
            self.assertEqual(result['channel_username'],'sample_channel')
            self.assertEqual(result['telegram_channel_id'],321)
        self.dialog.send.assert_not_called();self.dialog.click.assert_not_called()

    async def test_inactive_alias_group_and_mismatched_username_are_distinct(self):
        self.dialog.select=AsyncMock()
        for channel,code in [
            (NS(id=321,broadcast=False,username='sample_channel'),'channel_not_broadcast'),
            (NS(id=321,broadcast=True,username='primary',usernames=[NS(username='sample_channel',active=False)]),'channel_username_mismatch'),
        ]:
            self.client.get_entity.return_value=channel
            with self.assertRaisesRegex(BotProtocolError,code):await self.dialog.prepare(self.request)
        with self.assertRaisesRegex(BotProtocolError,'channel_link_invalid'):
            await self.dialog.prepare(dict(self.request,channel_url='https://t.me/+private'))
        self.dialog.select.assert_not_called()

    async def test_resolution_errors_do_not_hide_network_or_flood_wait(self):
        for name,code in [('UsernameNotOccupiedError','channel_not_found'),('ChannelPrivateError','channel_access_denied')]:
            self.client.get_entity.side_effect=type(name,(Exception,),{})('PRIVATE DETAILS')
            with self.assertRaisesRegex(BotProtocolError,'^'+code+'$'):
                await resolve_channel(self.client,'sample_channel')
        for exc in (OSError('network'),type('FloodWaitError',(Exception,),{})('wait')):
            self.client.get_entity.side_effect=exc
            with self.assertRaises(type(exc)) as caught:await resolve_channel(self.client,'sample_channel')
            self.assertIs(caught.exception,exc)

    async def test_similar_named_different_channel_does_not_verify(self):
        self.dialog.select=AsyncMock(return_value=card('other_channel'))
        self.dialog.click=AsyncMock();self.dialog.send=AsyncMock()
        self.client.get_entity.side_effect=lambda name: NS(id=321 if name=='@sample_channel' else 999,
                                                         broadcast=True,username=name[1:])
        with self.assertRaisesRegex(BotProtocolError,'set_promotion_button_missing'):
            await self.dialog.prepare(self.request)
        self.dialog.send.assert_not_called();self.dialog.click.assert_not_called()
