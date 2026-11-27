import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import test_postgres
from app import home_banner, main

class HomeBannerTests(unittest.TestCase):
 def test_persistence_validation_and_access(self):
  with tempfile.TemporaryDirectory() as folder, patch.object(home_banner,'FILE',Path(folder)/'banner.json'), TestClient(main.app) as client:
   main.app.dependency_overrides.clear()
   try:
    self.assertEqual(client.put('/api/v1/admin/home-banner',json={'title':'Hello'}).status_code,401)
    for role in ['advertiser','operator']:
     main.app.dependency_overrides[main.get_current_user]=lambda role=role:{'role':role,'id':'test'}
     self.assertEqual(client.put('/api/v1/admin/home-banner',json={'title':'Hello'}).status_code,403)
    main.app.dependency_overrides[main.get_current_user]=lambda:{'role':'admin','id':'test'}
    self.assertEqual(client.put('/api/v1/admin/home-banner',json={'title':' '*10}).status_code,422)
    self.assertEqual(client.put('/api/v1/admin/home-banner',json={'title':'a'*101}).status_code,422)
    self.assertEqual(client.put('/api/v1/admin/home-banner',json={'title':'  Наш прокси  ','subtitle':'Тест'}).status_code,200)
    self.assertEqual(client.get('/api/v1/public-config').json()['home_title'],'Наш прокси')
    self.assertEqual(home_banner.read()['subtitle'],'Тест')
    self.assertEqual(home_banner.read()['connect_label'],'Подключить')
    self.assertEqual(home_banner.read()['connect_caption'],'трафик')
    self.assertEqual(client.put('/api/v1/admin/home-banner',json={'title':'Наш прокси','connect_label':'   '}).status_code,422)
    self.assertEqual(client.put('/api/v1/admin/home-banner',json={'title':'Наш прокси','connect_label':'a'*25}).status_code,422)
    self.assertEqual(client.put('/api/v1/admin/home-banner',json={'title':'Наш прокси','connect_caption':'a'*25}).status_code,422)
    response=client.put('/api/v1/admin/home-banner',json={'title':'Наш прокси','subtitle':'Тест','connect_label':'  Подключить MTProxy  ','connect_caption':' '})
    self.assertEqual(response.status_code,200)
    self.assertEqual(response.json()['connect_label'],'Подключить MTProxy')
    self.assertEqual(response.json()['connect_caption'],'')
    config=client.get('/api/v1/public-config').json()
    self.assertEqual(config['home_connect_label'],'Подключить MTProxy')
    self.assertEqual(config['home_connect_caption'],'')
    self.assertEqual(client.put('/api/v1/admin/home-banner',json={'title':'Старый клиент','subtitle':'Тест'}).status_code,200)
    self.assertEqual(home_banner.read()['connect_label'],'Подключить MTProxy')
    self.assertEqual(home_banner.read()['connect_caption'],'')
    home_banner.FILE.write_text(json.dumps({'title':'Старый файл','subtitle':'Подпись'}),encoding='utf-8')
    self.assertEqual(home_banner.read()['title'],'Старый файл')
    self.assertEqual(home_banner.read()['connect_label'],'Подключить')
    self.assertEqual(home_banner.read()['connect_caption'],'трафик')
   finally: main.app.dependency_overrides.clear()
