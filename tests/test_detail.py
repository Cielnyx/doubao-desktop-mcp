import unittest
from unittest.mock import Mock, patch
from doubao_mcp.background import BackgroundError
from doubao_mcp.workflow import VideoWorkflow

class DetailWorkflowTests(unittest.TestCase):
 def setUp(self):
  self.w=VideoWorkflow(Mock(),Mock())
  self.preview={'url':'https://example.test/a?lr=watermark','source':'client_card_metadata','message_id':'m','task_id':'t'}
  self.formal={'url':'https://example.test/b?lr=unwatermarked','source':'rendered_video_player','message_id':'m','task_id':'t'}
  self.status={'status':'completed','conversation_id':'c','message_id':'m','task_id':'t','videos':[self.preview]}
  self.w.status=Mock(return_value=self.status)
 def test_default_refuses_missing_formal(self):
  with patch('doubao_mcp.detail.open_detail',side_effect=BackgroundError('DETAIL_OPEN_TIMEOUT','timeout')),patch('doubao_mcp.workflow.download_video') as download:
   with self.assertRaises(BackgroundError) as e:self.w.download('x')
   self.assertEqual(e.exception.code,'FORMAL_RESOURCE_UNAVAILABLE');download.assert_not_called()
 def test_explicit_preview(self):
  with patch('doubao_mcp.workflow.download_video',return_value={}) as download:
   result=self.w.download('x',resource_kind='preview')
   self.assertEqual(result['source_url'],download.call_args.args[0]);self.assertEqual(result['url_lr'],'watermark')
 def test_reorder_and_actual_url(self):
  refreshed={**self.status,'videos':[self.formal,self.preview]};self.w.status.side_effect=[self.status,refreshed]
  with patch('doubao_mcp.detail.open_detail',return_value={'resource':self.formal}),patch('doubao_mcp.workflow.download_video',return_value={}) as download:
   result=self.w.download('x');self.assertEqual(result['selected_video_index'],0);self.assertEqual(result['source_url'],download.call_args.args[0]);self.assertEqual(result['url_lr'],'unwatermarked')
 def test_invalid_index(self):
  for index in (True,-1,1.0):
   with self.subTest(index=index),self.assertRaises(BackgroundError):self.w.download('x',video_index=index)
 def test_reject_incompatible_index(self):
  self.w.status.side_effect=[self.status,{**self.status,'videos':[self.preview,self.formal]}]
  with patch('doubao_mcp.detail.open_detail',return_value={'resource':self.formal}),patch('doubao_mcp.workflow.download_video') as download:
   with self.assertRaises(BackgroundError):self.w.download('x',video_index=0)
   download.assert_not_called()

 def test_disabled_auto_detail_never_opens_or_downloads_a_preview(self):
  with patch('doubao_mcp.detail.open_detail') as opened,patch('doubao_mcp.detail._evaluate',return_value={'matches':[]}),patch('doubao_mcp.workflow.download_video') as download:
   with self.assertRaises(BackgroundError) as error:self.w.download('x',open_detail_if_needed=False)
   self.assertEqual(error.exception.code,'FORMAL_RESOURCE_UNAVAILABLE');opened.assert_not_called();download.assert_not_called()

 def test_unlisted_frozen_formal_source_does_not_invent_a_video_index(self):
  with patch('doubao_mcp.detail.open_detail',return_value={'resource':self.formal}),patch('doubao_mcp.workflow.download_video',return_value={}) as download:
   result=self.w.download('x')
   self.assertIsNone(result['selected_video_index']);self.assertEqual(result['source_url'],download.call_args.args[0]);self.assertEqual(result['source_url'],self.formal['url'])

 def test_invalid_detail_timeout_is_rejected_before_task_navigation(self):
  for timeout in (True,0,61,3.5):
   with self.subTest(timeout=timeout),self.assertRaises(BackgroundError):self.w.open_detail(message_id='m',timeout=timeout)
  self.w.status.assert_not_called()

class HoverSafetyTests(unittest.TestCase):
 def test_changed_hit_after_hover_never_presses(self):
  from doubao_mcp.detail import open_detail
  driver=Mock()
  status={'status':'completed','conversation_id':'c','message_id':'m','task_id':'t'}
  observations=[{'already_open':False,'point':{'x':1,'y':1}}, {'already_open':False,'point':{'x':1,'y':1}}, {'already_open':False,'point':{'x':2,'y':1}}]
  with patch('doubao_mcp.detail._evaluate',side_effect=observations):
   with self.assertRaises(BackgroundError) as e:open_detail(driver,status)
  self.assertEqual(e.exception.code,'DETAIL_BLOCKED')
  self.assertEqual(driver.client.call.call_count,1)
  self.assertEqual(driver.client.call.call_args.args[1]['type'],'mouseMoved')
