"""18+ — оценка публичного содержимого, обоснование и честные ограничения выборки."""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
from app import channel_content_review as content


class ContentReviewTests(unittest.TestCase):
    def test_explicit_markers_are_separate_from_industry_and_not_auto_approval(self):
        for text in ('Канал для взрослых, 18+.', '+18 Only sex stickers', '🔞 Маркировка канала',
                     'NSFW — только для взрослой аудитории.',
                     'Эротические рассказы для подписчиков.', 'Канал о кино и OnlyFans.'):
            with self.subTest(text=text):
                report = content.assess({'title':'Технологии', 'posts':[text]}, True)
                self.assertEqual(report['assessment'],'flagged')
                self.assertEqual(report['engine'],'rules')
                self.assertIn(text,report['evidence'][0]['text'])
                self.assertIn('контекст',report['detail'])
                self.assertFalse(report['media_analyzed'])
                self.assertIn('не решение модерации',report['limitation'])

    def test_news_medical_education_and_negation_keep_context_not_adult_verdict(self):
        for text in ('Обсуждаем закон о маркировке 18+ в новостях.',
                     'Медицинский курс для врачей 18+ об основах здоровья.',
                     'Порнография запрещена правилами нашего образовательного канала.'):
            report = content.assess({'posts':[text]}, True,
                {'assessment':'not_detected','evidence':[]})
            self.assertEqual(report['assessment'],'flagged')
            self.assertEqual(report['engine'],'rules')
            self.assertIn(text,report['evidence'][0]['text'])
            self.assertIn('образовательных',report['detail'])

    def test_missing_posts_never_means_no_adult_content(self):
        for channel in ({}, {'title':'Новости'}, {'posts':['   ']}):
            report = content.assess(channel, True, {'assessment':'not_detected','evidence':[]})
            self.assertEqual(report['assessment'],'uncertain')
            self.assertEqual(report['posts_count'],0)
            self.assertIn('недоступны',report['detail'])

    def test_failed_public_lookup_does_not_classify_unverified_title(self):
        report = content.assess({'title':'18+', 'posts':['18+']}, False)
        self.assertEqual(report['assessment'],'uncertain')
        self.assertEqual(report['evidence'],[])
        self.assertIn('не проверено',report['detail'])

    def test_no_ai_and_no_markers_is_uncertain_not_a_clean_bill(self):
        report = content.assess({'posts':['Сегодня изучаем сводные таблицы в Excel.']}, True)
        self.assertEqual(report['assessment'],'uncertain')
        self.assertIn('ИИ недоступен',report['detail'])
        self.assertEqual(report['engine'],'none')

    def test_ai_non_detection_is_limited_to_accessible_texts(self):
        report = content.assess({'posts':['Уроки Excel: как посчитать итог по строкам.']}, True,
                                {'assessment':'not_detected','evidence':[]})
        self.assertEqual(report['assessment'],'not_detected')
        self.assertEqual(report['posts_count'],1)
        self.assertIn('не распространяется',report['detail'])

    def test_grounded_quotes_use_server_text_and_surrounding_negation(self):
        text = 'Наша площадка запрещает взрослый контент. Сегодня разбираем правила.'
        report = content.assess({'posts':[text]}, True, {'assessment':'flagged','evidence':[
            {'source':'post_1','quote':'ВЗРОСЛЫЙ   контент'},
            {'source':'post_1','quote':'ВЗРОСЛЫЙ   контент'}]})
        self.assertEqual(report['engine'],'ai')
        self.assertEqual(len(report['evidence']),1)
        self.assertIn('запрещает взрослый контент',report['evidence'][0]['text'])
        self.assertEqual(report['evidence'][0]['source_label'],'Публикация 1')

    def test_hallucinated_wrong_source_empty_and_oversized_evidence_rejected(self):
        channel = {'title':'Канал о книгах','posts':['Обзор новой книги для читателей.']}
        for proposal in (
            {'assessment':'flagged','evidence':[]},
            {'assessment':'flagged','evidence':[{'source':'post_1','quote':'18+ только для взрослых'}]},
            {'assessment':'flagged','evidence':[{'source':'post_8','quote':'Обзор новой книги'}]},
            {'assessment':'flagged','evidence':[{'source':'private_messages','quote':'Обзор новой книги'}]},
            {'assessment':'flagged','evidence':[{'source':'post_1','quote':'x'*201}]},
            {'assessment':'flagged','evidence':[{'source':'post_1','quote':'   '}]},
            {'assessment':'uncertain','evidence':[{'source':'post_1','quote':'Обзор'}]*4},
            {'assessment':'approved','evidence':[]},
            {'assessment':'uncertain','evidence':[], 'execute':'approve'},
        ):
            with self.subTest(proposal=proposal),self.assertRaises(ValueError):
                content.assess(channel, True, proposal)

    def test_metadata_can_flag_but_cannot_claim_posts_were_reviewed(self):
        report = content.assess({'description':'Только для взрослых. 18+.'}, True)
        self.assertEqual(report['assessment'],'flagged')
        self.assertEqual(report['posts_count'],0)
        self.assertEqual(report['evidence'][0]['source'],'description')

    def test_source_limits_and_context_bound_quote_output(self):
        channel={'title':'a'*255+'18+', 'description':'b'*2000+'18+',
                 'posts':['c'*1200+'18+']*8+['18+']}
        report=content.assess(channel,True)
        self.assertEqual(report['assessment'],'uncertain')
        self.assertEqual(report['posts_count'],8)
        text='a'*400+' взрослый контент '+'b'*400
        report=content.assess({'posts':[text]},True,
            {'assessment':'flagged','evidence':[{'source':'post_1','quote':'взрослый контент'}]})
        self.assertLessEqual(len(report['evidence'][0]['text']),372)
        self.assertTrue(report['evidence'][0]['text'].startswith('…'))
        self.assertTrue(report['evidence'][0]['text'].endswith('…'))
