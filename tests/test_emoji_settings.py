import base64
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'api'))
from app import emoji_settings as icons
from fastapi import HTTPException

class EmojiSettingsTests(unittest.TestCase):
    def test_preview_is_png_without_token_or_metadata(self):
        out=io.BytesIO();Image.new('RGB',(100,100),'orange').save(out,format='PNG')
        info=json.dumps({'ok':True,'result':{'file_path':'stickers/test.png'}}).encode()
        with patch.object(icons,'read_response',side_effect=[info,out.getvalue()]):
            result=icons.preview('123456:fixture_token',{'file_id':'file'})
        self.assertTrue(result.startswith('data:image/png;base64,'))
        self.assertNotIn('fixture_token',result)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(result.split(',')[1]))).size,(100,100))

    def test_unsafe_file_path_never_downloaded(self):
        for path in ('../../secret.png','https://other.example/file.png','stickers/test.svg'):
            with patch.object(icons,'read_response',return_value=json.dumps({'ok':True,'result':{'file_path':path}}).encode()) as read:
                self.assertIsNone(icons.preview('123456:fixture_token',{'file_id':'file'}))
                self.assertEqual(read.call_count,1)

    def test_catalog_import_and_mapping_reject_foreign_ids(self):
        payload={'ok':True,'result':{'name':'PushokIcons','sticker_type':'custom_emoji','stickers':[{'custom_emoji_id':'12345','file_id':'file','emoji':'🐱'}]}}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{'PUSHOK_CONTROL_DIR':directory}), patch.object(icons,'credentials',return_value=('123456:fixture_token',123456)), patch.object(icons,'read_response',return_value=json.dumps(payload).encode()), patch.object(icons,'preview',return_value=None):
            self.assertEqual(icons.fetch()[0]['id'],'12345')
            icons.save({'help':'12345'})
            with self.assertRaises(HTTPException):
                icons.save({'help':'99999'})
            with self.assertRaises(HTTPException):
                icons.save({'unknown':'12345'})
